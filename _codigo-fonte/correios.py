"""Integracao com a API oficial dos Correios (rastreamento - SRO Rastro).

Fica desligada ate o arquivo correios_config.json ter "ativo": true e as
credenciais do contrato. Veja COMO CONFIGURAR A API DOS CORREIOS.txt.

Fluxo da API (https://api.correios.com.br):
  1. POST /token/v1/autentica/cartaopostagem  (Basic usuario:codigo_acesso,
     corpo {"numero": cartao_postagem}) -> {"token": ..., "expiraEm": ...}
  2. GET  /srorastro/v1/objetos?codigosObjetos=<codigo>&resultado=U
     (Bearer token) -> {"objetos": [{"codObjeto": ..., "eventos": [...]}]}
"""
import base64
import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from gerar_tabela_totvs import PASTA

CONFIG = PASTA / "correios_config.json"
CONFIG_PADRAO = {
    "ativo": False,
    "usuario": "",
    "codigo_acesso": "",
    "cartao_postagem": "",
    "url_base": "https://api.correios.com.br",
    "pausa_entre_consultas_seg": 0.3,
}

# status que nao mudam mais: nao consulta de novo
FINAIS = {"Entregue", "Retirado na Agência", "Retornou ao Remetente", "Pedido Cancelado",
          "Etiqueta Expirada", "Trocou de Código"}

_token = {"valor": None, "expira": datetime.min}


def finalizado(status):
    """Finalizado nao e consultado de novo; "Entregue - ..." com anotacao tambem conta."""
    return bool(status) and (status in FINAIS or status.lower().startswith("entregue"))


def carregar_config():
    if not CONFIG.exists():
        CONFIG.write_text(json.dumps(CONFIG_PADRAO, indent=2, ensure_ascii=False), encoding="utf-8")
    try:
        # strict=False aceita quebra de linha colada junto com o codigo de acesso
        lido = json.loads(CONFIG.read_text(encoding="utf-8-sig"), strict=False)
    except ValueError as e:
        raise ValueError(f"O correios_config.json esta com erro de digitacao ({e}). Confira aspas e virgulas.")
    cfg = {**CONFIG_PADRAO, **lido}
    for k in ("usuario", "codigo_acesso", "cartao_postagem", "url_base"):
        cfg[k] = "".join(str(cfg[k]).split())
    # CNPJ/CPF e cartao vao so com numeros: 12.345.678/0001-90 -> 12345678000190
    if re.fullmatch(r"[\d./-]+", cfg["usuario"]):
        cfg["usuario"] = re.sub(r"\D", "", cfg["usuario"])
    cfg["cartao_postagem"] = re.sub(r"\D", "", cfg["cartao_postagem"])
    return cfg


def situacao():
    try:
        cfg = carregar_config()
    except ValueError as e:
        return {"pronto": False, "motivo": str(e)}
    faltando = [k for k in ("usuario", "codigo_acesso") if not str(cfg[k]).strip()]
    if not cfg["ativo"]:
        return {"pronto": False, "motivo": "Integracao desligada (\"ativo\": false no correios_config.json)."}
    if faltando:
        return {"pronto": False, "motivo": "Faltam no correios_config.json: " + ", ".join(faltando)}
    return {"pronto": True, "motivo": ""}


class ErroCorreios(Exception):
    """`fatal` = vale para todos os rastreios (acesso, internet); senao e problema so daquele objeto."""

    def __init__(self, msg, fatal=True):
        super().__init__(msg)
        self.fatal = fatal


def _pedir(url, metodo="GET", corpo=None, cabecalhos=None):
    req = urllib.request.Request(url, method=metodo, headers={"Accept": "application/json", **(cabecalhos or {})},
                                 data=json.dumps(corpo).encode() if corpo is not None else None)
    if corpo is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", "replace")[:300]
        if "TOK-016" in detalhe:
            raise ErroCorreios("TOK-016: " + detalhe)
        if "GTW-012" in detalhe:
            raise ErroCorreios("Login aceito, mas a API de rastreamento (SRO Rastro, API 87) nao esta liberada para "
                               "esse usuario (GTW-012). Ela precisa ser liberada no contrato dos Correios: entre no "
                               "CWS com o login do contrato ou peca ao consultor comercial dos Correios.")
        if e.code in (401, 403):
            raise ErroCorreios(f"Correios recusaram o acesso ({e.code}). Confira usuario, codigo de acesso e cartao. {detalhe}")
        if e.code == 429:
            raise ErroCorreios("Muitas consultas seguidas (429). Aumente \"pausa_entre_consultas_seg\" no correios_config.json.", fatal=False)
        raise ErroCorreios(f"Erro {e.code} da API dos Correios: {detalhe}", fatal=e.code >= 500)
    except urllib.error.URLError as e:
        raise ErroCorreios(f"Sem conexao com a API dos Correios ({e.reason}). Confira a internet.")
    except (TimeoutError, OSError):
        raise ErroCorreios("Os Correios nao responderam a tempo. Confira a internet e tente de novo.")
    except ValueError:
        raise ErroCorreios("Resposta invalida da API dos Correios.", fatal=False)


def _obter_token(cfg):
    if _token["valor"] and datetime.now() < _token["expira"]:
        return _token["valor"]
    basic = {"Authorization": "Basic " + base64.b64encode(f"{cfg['usuario']}:{cfg['codigo_acesso']}".encode()).decode()}
    try:
        if not cfg["cartao_postagem"]:
            raise ErroCorreios("sem cartao")
        resp = _pedir(f"{cfg['url_base']}/token/v1/autentica/cartaopostagem", "POST",
                      {"numero": str(cfg["cartao_postagem"])}, basic)
    except ErroCorreios as erro_cartao:
        # login so com usuario + codigo de acesso, sem o cartao (ex.: TOK-016, cartao de outro CNPJ)
        try:
            resp = _pedir(f"{cfg['url_base']}/token/v1/autentica", "POST", {}, basic)
        except ErroCorreios:
            if "TOK-016" in str(erro_cartao):
                raise ErroCorreios("Login recusado (TOK-016): o cartao de postagem e de outro CNPJ, diferente do "
                                   "usuario do idCorreios. Use o login da empresa dona do contrato, com o codigo de "
                                   "acesso gerado nesse login, ou o cartao que aparece nesse login no CWS.")
            raise erro_cartao
    _token["valor"] = resp["token"]
    try:
        _token["expira"] = datetime.fromisoformat(resp["expiraEm"][:19]) - timedelta(minutes=5)
    except (KeyError, ValueError):
        _token["expira"] = datetime.now() + timedelta(minutes=50)
    return _token["valor"]


def _norm(s):
    s = unicodedata.normalize("NFD", str(s or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def status_do_evento(descricao):
    """Traduz a descricao do ultimo evento dos Correios para o status da tabela."""
    d = _norm(descricao)
    if "devolvido ao remetente" in d or "devolucao" in d or "entregue ao remetente" in d:
        return "Retornou ao Remetente"
    if "entregue" in d:
        return "Entregue"
    if "aguardando retirada" in d or "disponivel para retirada" in d:
        return "Aguardando retirada"
    if "saiu para entrega" in d:
        return "Saiu para entrega"
    if "postado" in d or "postagem" in d:
        return "Postado"
    if "transito" in d or "encaminhado" in d or "recebido" in d:
        return "Em trânsito"
    return descricao.strip() or "Em trânsito"


def consultar(rastreio, cfg=None):
    """Devolve {"status", "evento", "quando"} do ultimo evento do objeto."""
    cfg = cfg or carregar_config()
    token = _obter_token(cfg)
    resp = _pedir(f"{cfg['url_base']}/srorastro/v1/objetos?codigosObjetos={rastreio}&resultado=U",
                  cabecalhos={"Authorization": "Bearer " + token})
    objetos = resp.get("objetos") or []
    eventos = objetos[0].get("eventos") if objetos else None
    if not eventos:
        msg = objetos[0].get("mensagem", "") if objetos else ""
        return {"status": None, "evento": msg or "Sem eventos ainda", "quando": None}
    ev = eventos[0]
    return {"status": status_do_evento(ev.get("descricao", "")),
            "evento": ev.get("descricao", ""), "quando": ev.get("dtHrCriado")}


def pausa():
    time.sleep(float(carregar_config()["pausa_entre_consultas_seg"]))
