"""Programa visual: arraste o DadosEntregues (ou tabelas TOTVS antigas) e o historico de ITU e atualizado.

Roda como "Controle Correios Itu.exe" (gerado por _codigo-fonte/gerar_exe.bat): sobe um servidor
local em 127.0.0.1 e abre a tela numa janela do Chrome. Os dados podem ficar numa pasta do servidor
da rede, compartilhada por todos (veja travar()).

Cada envio do historico e identificado por "RASTREIO|ERP": uma mesma etiqueta
pode levar mais de um pedido. Pedido sem rastreio fica como "ERP-<numero>".
"""
import json
import os
import re
import subprocess
import threading
import time
import traceback
import urllib.request
import webbrowser
from contextlib import contextmanager
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import correios
import gerar_tabela_totvs as g
from gerar_tabela_totvs import RASTREIO_OK, RECURSOS, caminho_tabela, ler_arquivo, salvar_tabela

PORTA = int(os.environ.get("CORREIOS_PORTA", 8765))
SEM_NAVEGADOR = os.environ.get("CORREIOS_SEM_NAVEGADOR") == "1"  # usado so em teste
CONFIG_LOCAL = g.PASTA / "configuracao.json"
TELA = RECURSOS / "interface.html"
# versao.txt e o instalador sao gerados pelo gerar_exe.bat e publicados no GitHub
URL_GITHUB = "https://raw.githubusercontent.com/Z4ckxy/Correios/main"
URL_VERSAO = os.environ.get("CORREIOS_URL_VERSAO", URL_GITHUB + "/_codigo-fonte/versao.txt")
URL_INSTALADOR = os.environ.get("CORREIOS_URL_INSTALADOR", URL_GITHUB + "/instalador/Instalar%20Controle%20Correios%20Itu.exe")
CODIGO_CLIENTE = re.compile(r"^[A-Z0-9]{6}$")
_trava_local = threading.Lock()
sync = {"rodando": False, "etapa": None, "feitos": 0, "total": 0, "atualizados": 0, "falhas": 0,
        "ultimaFalha": None, "erro": None, "fim": None, "cancelar": False}
aviso_pasta = None  # preenchido quando a pasta configurada (ex.: Z:) nao esta acessivel


# ---------- pasta dos dados (local ou compartilhada no servidor) ----------
def arq_historico():
    return g.SAIDA / "historico_itu.json"


def arq_clientes():
    return g.SAIDA / "clientes_codigo_nome.json"


@contextmanager
def travar(espera=20):
    """Trava entre computadores: varias pessoas podem usar a mesma pasta no servidor.

    Cria um arquivo .trava na pasta dos dados; quem chega depois espera. Trava com
    mais de 60 s e considerada esquecida (programa que fechou no meio) e e removida.
    """
    with _trava_local:
        g.SAIDA.mkdir(parents=True, exist_ok=True)
        arq = g.SAIDA / ".trava"
        limite = time.time() + espera
        while True:
            try:
                fd = os.open(arq, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.environ.get('COMPUTERNAME', '?')} {datetime.now():%H:%M:%S}".encode())
                os.close(fd)
                break
            except FileExistsError:
                try:
                    if time.time() - arq.stat().st_mtime > 60:
                        arq.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.time() > limite:
                    raise ValueError("Outra pessoa esta salvando no servidor agora. Tente de novo em alguns segundos.")
                time.sleep(0.2)
        try:
            yield
        finally:
            try:
                arq.unlink(missing_ok=True)
            except OSError:
                pass


def versao_dados():
    try:
        return arq_historico().stat().st_mtime_ns
    except OSError:
        return 0


def caminho_de_rede(p):
    """Z:\\pasta -> \\\\servidor\\Dados\\pasta, para funcionar em PC com outra letra de unidade."""
    p = Path(p)
    letra = p.drive
    if len(letra) == 2 and letra[1] == ":":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(1024)
            tam = ctypes.c_ulong(len(buf))
            if ctypes.windll.mpr.WNetGetConnectionW(letra, buf, ctypes.byref(tam)) == 0:
                return Path(buf.value + str(p)[2:])
        except (AttributeError, OSError):
            pass
    return p


def ler_config_local():
    try:
        return json.loads(CONFIG_LOCAL.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def aplicar_pasta_configurada():
    """Usa a pasta escolhida em configuracao.json; se ela nao abrir (servidor fora), usa a Saida local."""
    global aviso_pasta
    pasta = ler_config_local().get("pasta_dados")
    if not pasta:
        return
    try:
        Path(pasta).mkdir(parents=True, exist_ok=True)
        g.SAIDA = Path(pasta)
        aviso_pasta = None
    except OSError:
        aviso_pasta = (f"Nao consegui abrir a pasta {pasta}. Confira se o servidor (Z:) esta conectado. "
                       f"Por enquanto os dados estao sendo salvos neste computador, em {g.SAIDA}.")


def mesclar_em(destino):
    """Leva os envios desta pasta para a pasta nova sem apagar o que ja existe la."""
    origem_hist, origem_cli = carregar(), carregar_clientes()
    antiga = g.SAIDA
    g.SAIDA = destino
    with travar():
        hist, clientes = carregar(), carregar_clientes()
        novos = 0
        for id_, e in origem_hist.items():
            atual = hist.get(id_)
            if atual is None:
                hist[id_] = e
                novos += 1
                continue
            for campo in ("cliente", "status", "codCliente", "vendedor"):
                if e.get(campo) and not atual.get(campo):
                    atual[campo] = e[campo]
        for k, v in origem_cli.items():
            clientes.setdefault(k, v)
        gravar(hist)
        gravar(clientes, arq_clientes())
    return antiga, novos, len(hist)


def escolher_pasta(titulo):
    """Abre a janela do Windows para escolher uma pasta; devolve o caminho ou None."""
    import tkinter
    from tkinter import filedialog
    raiz = tkinter.Tk()
    raiz.withdraw()
    raiz.attributes("-topmost", True)
    try:
        escolhida = filedialog.askdirectory(title=titulo, mustexist=False, parent=raiz)
    finally:
        raiz.destroy()
    return Path(escolhida) if escolhida else None


def trocar_pasta(pasta=None):
    global aviso_pasta
    destino = Path(pasta) if pasta else escolher_pasta("Escolha a pasta dos dados (ex.: Z:\\Correios Itu)")
    if not destino:
        return {"ok": False, "cancelado": True}
    destino = caminho_de_rede(destino)
    try:
        destino.mkdir(parents=True, exist_ok=True)
        teste = destino / ".teste_escrita"
        teste.write_text("ok", encoding="utf-8")
        teste.unlink()
    except OSError as e:
        raise ValueError(f"Nao consigo gravar em {destino}: {e}")
    antiga, novos, total = mesclar_em(destino)
    cfg = ler_config_local()
    cfg["pasta_dados"] = str(destino)
    CONFIG_LOCAL.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    aviso_pasta = None
    with travar():
        hist = carregar()
        regravar_dias(hist, {e["data"] for e in hist.values()})
    return {"ok": True, "pasta": str(destino), "anterior": str(antiga), "levados": novos, "total": total}


def exportar_tudo(pasta=None):
    """Grava tudo numa pasta escolhida: Ano / Mes / tabela de cada dia, mais um arquivo do mes e um geral."""
    destino = Path(pasta) if pasta else escolher_pasta("Escolha onde salvar todas as tabelas")
    if not destino:
        return {"ok": False, "cancelado": True}
    hist = carregar()
    if not hist:
        raise ValueError("O historico esta vazio.")
    por_dia, por_mes = {}, {}
    for e in hist.values():
        por_dia.setdefault(e["data"], []).append(e)
        por_mes.setdefault(e["data"][:7], []).append(e)
    abertos = []
    for d, envios in por_dia.items():
        try:
            salvar_tabela(date.fromisoformat(d), envios, destino=caminho_tabela(date.fromisoformat(d), destino))
        except PermissionError:
            abertos.append(d)
    for m, envios in por_mes.items():
        dia = date.fromisoformat(m + "-01")
        nome_mes = g.MESES[dia.month - 1]
        arq = caminho_tabela(dia, destino).parent / f"Mes completo - {nome_mes} {dia.year}.xlsx"
        try:
            salvar_tabela(dia, envios, destino=arq, titulo=f"CORREIOS ITU - TOTVS - {nome_mes.upper()} {dia.year}")
        except PermissionError:
            abertos.append(m)
    geral = destino / "Historico completo - Correios Itu.xlsx"
    try:
        salvar_tabela(date.today(), list(hist.values()), destino=geral, titulo="CORREIOS ITU - TOTVS - HISTORICO COMPLETO")
    except PermissionError:
        abertos.append("geral")
    os.startfile(destino)
    return {"ok": True, "pasta": str(destino), "dias": len(por_dia), "meses": len(por_mes), "abertos": abertos}


def carregar():
    try:
        return json.loads(arq_historico().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def gravar(dados, caminho=None):
    caminho = caminho or arq_historico()
    g.SAIDA.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(caminho)


def chave(e):
    return f"{e['rastreio']}|{e['erp']}"


def carregar_clientes():
    """Codigo do cliente no TOTVS -> nome, aprendido das tabelas e das edicoes na tela."""
    try:
        return json.loads(arq_clientes().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def eh_codigo(s):
    return bool(CODIGO_CLIENTE.match(str(s or "")))


def aprender_cliente(hist, clientes, codigo, nome):
    """Guarda codigo -> nome e troca o codigo pelo nome nos outros envios. Devolve os dias alterados."""
    clientes[codigo] = nome
    dias = set()
    for e in hist.values():
        if e.get("codCliente") == codigo and (not e.get("cliente") or eh_codigo(e["cliente"])):
            e["cliente"] = nome
            dias.add(e["data"])
    return dias


def regravar_dias(hist, dias):
    """Regrava a tabela de cada dia; devolve (arquivos, dias que estavam abertos no Excel)."""
    arquivos, abertos = [], []
    por_dia = {}
    for e in hist.values():
        if e["data"] in dias:
            por_dia.setdefault(e["data"], []).append(e)
    for d in sorted(dias):
        try:
            arquivos.append(salvar_tabela(date.fromisoformat(d), por_dia.get(d, [])).name)
        except PermissionError:
            abertos.append(d)
    return arquivos, abertos


def guardar_sem_rastreio(hist, e, agora):
    """Pedido sem rastreio (aguardando codigo ou cancelado) fica guardado pela chave ERP-<numero>."""
    if not e["erp"] or any(x["erp"] == e["erp"] and RASTREIO_OK.match(x["rastreio"]) for x in hist.values()):
        return None
    id_ = "ERP-" + e["erp"]
    if id_ in hist:
        return None
    cancelado = "CANCEL" in e["rastreio"] or e.get("status") == "Pedido Cancelado"
    hist[id_] = {**e, "id": id_, "rastreio": "PEDIDO CANCELADO" if cancelado else "AGUARDANDO CÓDIGO",
                 "status": e.get("status") or ("Pedido Cancelado" if cancelado else ""), "importadoEm": agora}
    return id_


def importar(nome, conteudo):
    tipo, validos, sem_rastreio = ler_arquivo(nome, conteudo)
    with travar():
        hist = carregar()
        clientes = carregar_clientes()
        agora = datetime.now().isoformat(timespec="seconds")
        novos, completados, aguardando, dias = 0, 0, 0, set()
        # o envio e reconhecido pelo ERP: pedido que ja tem rastreio nao ganha outro
        com_rastreio = {x["erp"]: x for x in hist.values() if x["erp"] and RASTREIO_OK.match(x["rastreio"])}
        for e in validos:
            if tipo == "dados" and e["cliente"]:
                # o DadosEntregues traz o codigo; troca pelo nome se ja for conhecido
                e["codCliente"] = e["cliente"]
                e["cliente"] = clientes.get(e["cliente"], e["cliente"])
            id_ = chave(e)
            atual = hist.get(id_) or com_rastreio.get(e["erp"])
            if atual is None:
                espera = hist.pop("ERP-" + e["erp"], None) if e["erp"] else None
                if espera:
                    # chegou o rastreio de um pedido que estava aguardando codigo
                    e["cliente"] = e["cliente"] if e["cliente"] and not eh_codigo(e["cliente"]) else espera.get("cliente") or e["cliente"]
                    e["status"] = e.get("status") or ("" if espera.get("status") == "Pedido Cancelado" else espera.get("status", ""))
                    dias.add(espera["data"])
                hist[id_] = {**e, "id": id_, "status": e.get("status", ""), "importadoEm": agora}
                if e["erp"]:
                    com_rastreio[e["erp"]] = hist[id_]
                if espera:
                    completados += 1
                else:
                    novos += 1
                dias.add(e["data"])
                continue

            # envio ja conhecido: so completa o que estiver faltando
            mudou = False
            sem_nome = not atual.get("cliente") or eh_codigo(atual["cliente"])
            if tipo == "tabela" and e["cliente"] and not eh_codigo(e["cliente"]) and sem_nome:
                codigo = atual.get("codCliente") or (atual.get("cliente") if eh_codigo(atual.get("cliente")) else None)
                atual["cliente"] = e["cliente"]
                if codigo:
                    atual["codCliente"] = codigo
                    dias |= aprender_cliente(hist, clientes, codigo, e["cliente"])
                mudou = True
            elif tipo == "dados" and e.get("codCliente") and not atual.get("codCliente"):
                # o mesmo envio ja veio de uma tabela com o nome: aprende o codigo
                atual["codCliente"] = e["codCliente"]
                if not sem_nome:
                    dias |= aprender_cliente(hist, clientes, e["codCliente"], atual["cliente"])
                else:
                    atual["cliente"] = e["cliente"]
                mudou = True
            if e.get("status") and not atual.get("status"):
                atual["status"] = e["status"]
                mudou = True
            if not atual.get("vendedor") and e["vendedor"]:
                atual["vendedor"] = e["vendedor"]
                mudou = True
            if mudou:
                completados += 1
                dias.add(atual["data"])
        for e in sem_rastreio:
            if guardar_sem_rastreio(hist, e, agora):
                aguardando += 1
                dias.add(e["data"])
        gravar(hist)
        gravar(clientes, arq_clientes())
        arquivos, abertos = regravar_dias(hist, dias)

    return {
        "arquivo": nome,
        "tipo": tipo,
        "novos": novos,
        "completados": completados,
        "repetidos": len(validos) - novos - completados,
        "aguardando": aguardando,
        "diasAtualizados": sorted(dias),
        "arquivos": arquivos,
        "abertos": abertos,
    }


def mudar_status(ids, status):
    with travar():
        hist = carregar()
        dias = set()
        for i in ids:
            if i in hist:
                hist[i]["status"] = status
                hist[i].pop("origemStatus", None)
                dias.add(hist[i]["data"])
        gravar(hist)
        _, abertos = regravar_dias(hist, dias)
    aviso = None
    if abertos:
        aviso = "Status salvo, mas a planilha do dia esta aberta no Excel. Feche e mude qualquer status de novo para atualizar o arquivo."
    return {"ok": True, "aviso": aviso}


def mudar_cliente(id_, nome):
    with travar():
        hist = carregar()
        e = hist.get(id_)
        if e is None:
            raise ValueError("Envio nao encontrado.")
        e["cliente"] = nome
        dias = {e["data"]}
        outros = 0
        if e.get("codCliente") and nome and not eh_codigo(nome):
            clientes = carregar_clientes()
            novos_dias = aprender_cliente(hist, clientes, e["codCliente"], nome)
            outros = sum(1 for x in hist.values() if x.get("codCliente") == e["codCliente"] and x["data"] in novos_dias and x is not e)
            dias |= novos_dias
            gravar(clientes, arq_clientes())
        gravar(hist)
        _, abertos = regravar_dias(hist, dias)
    aviso = "Cliente salvo, mas a planilha do dia esta aberta no Excel." if abertos else None
    return {"ok": True, "outros": outros, "aviso": aviso}


def mudar_rastreio(id_, novo):
    """Troca o codigo de rastreio; como o id e "RASTREIO|ERP", o envio ganha id novo."""
    novo = re.sub(r"\s+", "", novo).upper()
    if not RASTREIO_OK.match(novo):
        raise ValueError("Codigo invalido. O formato e 2 letras, 9 numeros e 2 letras (ex.: AB123456789BR).")
    with travar():
        hist = carregar()
        e = hist.get(id_)
        if e is None:
            raise ValueError("Envio nao encontrado.")
        if novo == e["rastreio"]:
            return {"ok": True, "id": id_, "aviso": None}
        novo_id = f"{novo}|{e['erp']}"
        if novo_id in hist:
            raise ValueError(f"Ja existe um envio com o rastreio {novo} para o ERP {e['erp']}.")
        del hist[id_]
        e.update(rastreio=novo, id=novo_id)
        # o que veio dos Correios era do codigo antigo
        for campo in ("ultimoEvento", "ultimoEventoEm", "consultadoEm"):
            e.pop(campo, None)
        if e.pop("origemStatus", None) == "correios" or e.get("status") in ("Pedido Cancelado", "Trocou de Código"):
            e["status"] = ""
        hist[novo_id] = e
        gravar(hist)
        _, abertos = regravar_dias(hist, {e["data"]})
    aviso = "Rastreio salvo, mas a planilha do dia esta aberta no Excel." if abertos else None
    return {"ok": True, "id": novo_id, "aviso": aviso}


def refazer_dia(dia):
    """Grava de novo a tabela do dia a partir do historico (ex.: planilha apagada ou mexida a mao)."""
    try:
        date.fromisoformat(dia)
    except ValueError:
        raise ValueError("Escolha um dia na lista.")
    with travar():
        hist = carregar()
        n = sum(1 for e in hist.values() if e["data"] == dia)
        _, abertos = regravar_dias(hist, {dia})
    if abertos:
        raise ValueError("A planilha do dia esta aberta no Excel. Feche e tente de novo.")
    return {"ok": True, "envios": n, "arquivo": caminho_tabela(date.fromisoformat(dia)).name}


def exportar(titulo, ids):
    hist = carregar()
    envios = [hist[i] for i in ids if i in hist]
    if not envios:
        raise ValueError("Nada para exportar com esse filtro.")
    nome = re.sub(r'[<>:"/\\|?*]', "-", titulo).strip()
    destino = g.SAIDA / "Relatorios" / f"{nome}.xlsx"
    salvar_tabela(date.today(), envios, destino=destino, titulo=titulo)
    os.startfile(destino)
    return {"ok": True, "arquivo": destino.name}


def rodar_sync(rastreios, cfg):
    """Consulta os Correios em segundo plano e grava o status de cada objeto.

    Erro de um objeto so pula aquele rastreio; erro de acesso/internet (fatal) para tudo,
    e 10 falhas seguidas tambem param, para nao ficar rodando a toa.
    Os resultados sao gravados em lotes de 20 (o historico pode estar na rede).
    """
    dias = set()
    seguidas = 0
    pendentes = {}  # rastreio -> resultado ainda nao gravado

    def gravar_lote():
        if not pendentes:
            return
        agora = datetime.now().isoformat(timespec="seconds")
        with travar():
            hist = carregar()
            for e in hist.values():
                res = pendentes.get(e["rastreio"])
                if not res:
                    continue
                e["ultimoEvento"] = res["evento"]
                e["ultimoEventoEm"] = res["quando"]
                e["consultadoEm"] = agora
                if res["status"] and res["status"] != e.get("status"):
                    e["status"] = res["status"]
                    e["origemStatus"] = "correios"
                    dias.add(e["data"])
                    sync["atualizados"] += 1
            gravar(hist)
        pendentes.clear()

    try:
        for r in rastreios:
            if sync.get("cancelar"):
                sync["erro"] = "Consulta interrompida por voce."
                break
            try:
                res = correios.consultar(r, cfg)
                seguidas = 0
            except correios.ErroCorreios as err:
                if err.fatal:
                    raise
                sync["falhas"] += 1
                sync["ultimaFalha"] = f"{r}: {err}"
                seguidas += 1
                sync["feitos"] += 1
                if seguidas >= 10:
                    raise correios.ErroCorreios(f"10 rastreios seguidos deram erro. Ultimo: {err}")
                correios.pausa()
                continue
            pendentes[r] = res
            if len(pendentes) >= 20:
                gravar_lote()
            sync["feitos"] += 1
            correios.pausa()
    except correios.ErroCorreios as err:
        sync["erro"] = str(err)
    except Exception as err:  # noqa: BLE001 - mostrar qualquer falha na tela
        sync["erro"] = f"Falha inesperada: {err}"
    finally:
        try:
            gravar_lote()
            with travar():
                regravar_dias(carregar(), dias)
        except Exception as err:  # noqa: BLE001
            sync["erro"] = sync["erro"] or f"Nao consegui salvar o resultado: {err}"
        sync.update(rodando=False, etapa=None, fim=datetime.now().isoformat(timespec="seconds"))


def iniciar_sync(ids=None):
    if sync["rodando"]:
        raise ValueError("Ja existe uma atualizacao em andamento.")
    sit = correios.situacao()
    if not sit["pronto"]:
        raise ValueError(sit["motivo"])
    hist = carregar()
    if ids:
        envios = [hist[i] for i in ids if i in hist]
    else:
        envios = [e for e in hist.values() if not correios.finalizado(e.get("status"))]
    alvo = sorted({e["rastreio"] for e in envios if RASTREIO_OK.match(e["rastreio"])})
    if not alvo:
        raise ValueError("Nenhum envio pendente para consultar.")

    # testa login e um rastreio antes de comecar: erro de acesso aparece na hora, sem ficar "em 0"
    cfg = correios.carregar_config()
    sync.update(rodando=True, etapa="testando", feitos=0, total=len(alvo), atualizados=0, falhas=0,
                ultimaFalha=None, erro=None, fim=None, cancelar=False)
    try:
        correios.consultar(alvo[0], cfg)
    except correios.ErroCorreios as err:
        if err.fatal:
            sync.update(rodando=False, etapa=None, erro=str(err), fim=datetime.now().isoformat(timespec="seconds"))
            raise ValueError(str(err))
    sync["etapa"] = "consultando"
    threading.Thread(target=rodar_sync, args=(alvo, cfg), daemon=True).start()
    return {"ok": True, "total": len(alvo)}


# ---------- atualizacao do programa (instalador publicado no GitHub) ----------
def versao_atual():
    try:
        return (RECURSOS / "versao.txt").read_text(encoding="utf-8").strip() or "0"
    except OSError:
        return "0"


def _num(v):
    return tuple(int(p) for p in re.findall(r"\d+", v or ""))


atualizacao = {"atual": versao_atual(), "nova": None, "verificando": False, "erro": None}


def checar_atualizacao():
    """Le a versao publicada no GitHub; se for maior que a deste programa, a tela oferece atualizar."""
    atualizacao.update(verificando=True, erro=None)
    try:
        req = urllib.request.Request(URL_VERSAO, headers={"User-Agent": "ControleCorreiosItu", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=10) as r:
            publicada = r.read(100).decode("utf-8", "replace").strip()
        if re.fullmatch(r"[\d.]+", publicada) and _num(publicada) > _num(atualizacao["atual"]):
            atualizacao["nova"] = publicada
    except (OSError, ValueError) as e:
        atualizacao["erro"] = f"Nao consegui ver se tem versao nova: {e}"
    finally:
        atualizacao["verificando"] = False


def instalar_atualizacao():
    """Baixa o instalador novo e roda ele em modo silencioso depois que este programa fechar."""
    if not atualizacao["nova"]:
        raise ValueError("Nao tem versao nova para instalar.")
    if sync["rodando"]:
        raise ValueError("Espere a consulta dos Correios terminar (ou clique em Parar) antes de atualizar.")
    destino = Path(os.environ.get("TEMP", str(g.PASTA))) / "Instalar Controle Correios Itu.exe"
    try:
        req = urllib.request.Request(URL_INSTALADOR, headers={"User-Agent": "ControleCorreiosItu"})
        with urllib.request.urlopen(req, timeout=120) as r:
            dados = r.read()
    except OSError as e:
        raise ValueError(f"Nao consegui baixar a versao nova: {e}")
    if not dados.startswith(b"MZ") or len(dados) < 1_000_000:
        raise ValueError("O arquivo baixado nao parece o instalador. Tente de novo mais tarde.")
    destino.write_bytes(dados)
    # espera este programa fechar para o instalador poder trocar o .exe; o instalador abre o programa de novo
    caminho = str(destino).replace("'", "''")
    subprocess.Popen(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command",
         f"Start-Sleep 3; Start-Process -FilePath '{caminho}' -ArgumentList '/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'"],
        creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)


def encerrar():
    chrome = getattr(Handler, "chrome", None)
    if chrome and chrome.poll() is None:
        chrome.terminate()
    if getattr(Handler, "servidor", None):
        threading.Thread(target=Handler.servidor.shutdown, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, status=200):
        corpo = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            corpo = TELA.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
        elif self.path == "/historico":
            self._json(list(carregar().values()))
        elif self.path == "/correios":
            self._json({**correios.situacao(), **sync})
        elif self.path == "/atualizacao":
            self._json(atualizacao)
        elif self.path == "/versao":
            self._json({"versao": versao_dados()})
        elif self.path == "/pasta":
            self._json({"pasta": str(g.SAIDA), "padrao": str(g.PASTA / "Saida"),
                        "rede": str(g.SAIDA).startswith("\\\\"), "aviso": aviso_pasta})
        else:
            self.send_error(404)

    def do_POST(self):
        tamanho = int(self.headers.get("Content-Length", 0))
        conteudo = self.rfile.read(tamanho)
        try:
            if self.path == "/importar":
                nome = unquote(self.headers.get("X-Nome-Arquivo", "dados.csv"))
                self._json(importar(nome, conteudo))
                return
            pedido = json.loads(conteudo or b"{}")
            if self.path == "/status":
                self._json(mudar_status(pedido.get("ids", []), str(pedido.get("status", "")).strip()))
            elif self.path == "/cliente":
                self._json(mudar_cliente(str(pedido.get("id", "")), " ".join(str(pedido.get("cliente", "")).split())))
            elif self.path == "/refazer":
                self._json(refazer_dia(str(pedido.get("dia", ""))))
            elif self.path == "/rastreio":
                self._json(mudar_rastreio(str(pedido.get("id", "")), str(pedido.get("rastreio", ""))))
            elif self.path == "/exportar":
                self._json(exportar(str(pedido.get("titulo", "Relatorio")), pedido.get("ids", [])))
            elif self.path == "/sair":
                self._json({"ok": True})
                encerrar()
            elif self.path == "/atualizacao/instalar":
                instalar_atualizacao()
                self._json({"ok": True})
                encerrar()
            elif self.path == "/pasta/trocar":
                self._json(trocar_pasta(pedido.get("pasta")))
            elif self.path == "/exportar-tudo":
                self._json(exportar_tudo(pedido.get("pasta")))
            elif self.path == "/correios/parar":
                sync["cancelar"] = True
                self._json({"ok": True})
            elif self.path == "/correios/atualizar":
                self._json(iniciar_sync(pedido.get("ids")))
            elif self.path == "/abrir":
                if pedido.get("dia"):
                    alvo = caminho_tabela(date.fromisoformat(pedido["dia"]))
                    if not alvo.exists():
                        do_dia = [e for e in carregar().values() if e["data"] == pedido["dia"]]
                        salvar_tabela(date.fromisoformat(pedido["dia"]), do_dia)
                elif pedido.get("config"):
                    alvo = correios.criar_config()
                else:
                    g.SAIDA.mkdir(parents=True, exist_ok=True)
                    alvo = g.SAIDA
                os.startfile(alvo)
                self._json({"ok": True})
            else:
                self.send_error(404)
        except ValueError as e:
            self._json({"erro": str(e)}, 400)
        except PermissionError:
            self._json({"erro": "O arquivo esta aberto no Excel. Feche e tente de novo."}, 409)

    def log_message(self, *args):
        pass


def achar_chrome():
    candidatos = [
        Path(os.environ.get(v, "")) / "Google" / "Chrome" / "Application" / "chrome.exe"
        for v in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
    ]
    try:
        import winreg
        for raiz in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(raiz, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as k:
                    candidatos.insert(0, Path(winreg.QueryValue(k, None)))
            except OSError:
                pass
    except ImportError:
        pass
    return next((c for c in candidatos if c.is_file()), None)


def abrir_janela(url):
    """Abre o Chrome como janela de aplicativo (sem barra de endereco) num perfil proprio; devolve o processo ou None."""
    chrome = achar_chrome()
    if not chrome:
        return None
    # perfil separado: garante uma janela nova so do programa mesmo com o Chrome ja aberto
    perfil = Path(os.environ.get("LOCALAPPDATA", str(g.SAIDA))) / "ControleCorreiosItu" / "chrome"
    perfil.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([str(chrome), f"--app={url}", "--start-maximized", f"--user-data-dir={perfil}",
                             "--no-first-run", "--no-default-browser-check", "--disable-translate"])


def main():
    aplicar_pasta_configurada()
    url = f"http://127.0.0.1:{PORTA}/"
    try:
        servidor = ThreadingHTTPServer(("127.0.0.1", PORTA), Handler)
    except OSError:
        # ja tem um aberto: so mostra a tela de novo
        if not abrir_janela(url):
            webbrowser.open(url)
        return
    Handler.servidor = servidor
    threading.Thread(target=checar_atualizacao, daemon=True).start()
    print(f"Controle Correios Itu rodando em {url}")
    print(f"Pasta dos dados: {g.SAIDA}")

    if SEM_NAVEGADOR:
        servidor.serve_forever()
        return
    chrome = abrir_janela(url)
    if chrome:
        Handler.chrome = chrome

        def fechar_junto():
            # fechou a janela: encerra o programa tambem
            chrome.wait()
            servidor.shutdown()
        threading.Thread(target=fechar_junto, daemon=True).start()
    else:
        print("Chrome nao encontrado; abrindo no navegador padrao. Para sair, use o botao Fechar programa.")
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    servidor.serve_forever()


def principal():
    try:
        main()
    except Exception:  # noqa: BLE001 - sem console no .exe, o erro vai para um arquivo
        g.SAIDA.mkdir(parents=True, exist_ok=True)
        (g.SAIDA / "erro.log").write_text(traceback.format_exc(), encoding="utf-8")
        raise


if __name__ == "__main__":
    principal()
