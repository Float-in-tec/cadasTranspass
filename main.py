"""
CadasTranspass — Backend principal.
Stack: FastAPI + gspread + Jinja2 + PicoCSS (CDN)
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from functools import lru_cache
from typing import Annotated

import gspread
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from oauth2client.service_account import ServiceAccountCredentials

# ──────────────────────────────────────────────────────────────────────────────
# Configuração de ambiente
# ──────────────────────────────────────────────────────────────────────────────
load_dotenv()  # só tem efeito local; em produção as envvars já estão definidas

SPREADSHEET_ID: str = os.environ["SPREADSHEET_ID"]
SCOPES: list[str] = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

# Nome da aba principal de cadastros
ABA_CADASTRO = "Cadastro"

# Colunas esperadas na aba Cadastro (ordem importa para append_row)
COLUNAS_CADASTRO = [
    "Timestamp",
    "CPF",
    "Data_Nascimento",
    "Nome_Social",
    "Nome_Registro",
    "Identidade_Genero",
    "Raca_Etnia",
    "Inicio_Transicao",
    "PCD",
    "Rede_Social",
    "Declaracao_Verdade",
    "Status",
]

# ──────────────────────────────────────────────────────────────────────────────
# Conexão com Google Sheets (singleton via lru_cache)
# ──────────────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _get_client() -> gspread.Client:
    """
    Retorna um cliente gspread autenticado.

    Estratégia de credenciais (em ordem de prioridade):
    1. Variável GOOGLE_CREDENTIALS_JSON  → conteúdo JSON como string
       (ideal para Render.com e ambientes sem sistema de arquivos persistente)
    2. Variável GOOGLE_CREDENTIALS_PATH  → caminho para credentials.json local
    """
    creds_json_str = os.environ.get("GOOGLE_CREDENTIALS_JSON")

    if creds_json_str:
        # Escreve num arquivo temporário para que o oauth2client possa lê-lo
        creds_dict = json.loads(creds_json_str)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            json.dump(creds_dict, tmp)
            tmp_path = tmp.name
        creds = ServiceAccountCredentials.from_json_keyfile_name(tmp_path, SCOPES)
        os.unlink(tmp_path)
    else:
        creds_path = os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")
        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, SCOPES)

    return gspread.authorize(creds)


def get_spreadsheet() -> gspread.Spreadsheet:
    return _get_client().open_by_key(SPREADSHEET_ID)


def get_worksheet(nome_aba: str) -> gspread.Worksheet:
    return get_spreadsheet().worksheet(nome_aba)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_RE_CPF = re.compile(r"\D")  # remove qualquer não-dígito


def sanitize_cpf(cpf_raw: str) -> str:
    """Remove pontos, traços e espaços; retorna apenas os dígitos."""
    return _RE_CPF.sub("", cpf_raw.strip())


def timestamp_br() -> str:
    """Retorna timestamp no fuso UTC-3 (horário de Brasília)."""
    from datetime import timedelta

    brt = timezone(timedelta(hours=-3))
    return datetime.now(tz=brt).strftime("%d/%m/%Y %H:%M:%S")


def buscar_cadastro(cpf: str, data_nascimento: str) -> dict | None:
    """
    Busca uma linha na aba Cadastro pelo CPF (coluna B) e Data de Nascimento (coluna C).
    Retorna um dicionário com os dados ou None se não encontrado.
    """
    ws = get_worksheet(ABA_CADASTRO)
    registros = ws.get_all_records()
    for linha in registros:
        cpf_planilha = sanitize_cpf(str(linha.get("CPF", "")))
        nasc_planilha = str(linha.get("Data_Nascimento", "")).strip()
        if cpf_planilha == cpf and nasc_planilha == data_nascimento:
            return linha
    return None


def _identidade_eh_cis(identidade: str) -> bool:
    return bool(re.search(r"cis|cisgên", identidade, re.IGNORECASE))


def _garantir_aba_evento(titulo_aba: str) -> gspread.Worksheet:
    """Cria a aba do evento do dia se ainda não existir; retorna o worksheet."""
    planilha = get_spreadsheet()
    try:
        return planilha.worksheet(titulo_aba)
    except gspread.exceptions.WorksheetNotFound:
        ws = planilha.add_worksheet(title=titulo_aba, rows=1000, cols=3)
        ws.append_row(["Timestamp", "Nome_Social", "Aceite_Imagem"])
        return ws


# ──────────────────────────────────────────────────────────────────────────────
# Aplicação FastAPI
# ──────────────────────────────────────────────────────────────────────────────

app = FastAPI(title="CadasTranspass", version="1.0.0")
templates = Jinja2Templates(directory="templates")


# ── GET / — Tela inicial / login ──────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def tela_login(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("login.html", {"request": request})


# ── POST /consultar — Verificar cadastro existente ────────────────────────────
@app.post("/consultar", response_class=HTMLResponse)
async def consultar(
    request: Request,
    cpf: Annotated[str, Form()],
    data_nascimento: Annotated[str, Form()],
) -> HTMLResponse:
    cpf_limpo = sanitize_cpf(cpf)
    registro = buscar_cadastro(cpf_limpo, data_nascimento)

    if registro is None:
        # Não encontrado → redireciona para formulário de cadastro com dados pré-preenchidos
        url = f"/cadastro?cpf={cpf_limpo}&data_nascimento={data_nascimento}"
        return RedirectResponse(url=url, status_code=303)

    status = str(registro.get("Status", "")).strip()

    if status == "Aprovado":
        return templates.TemplateResponse(
            "carteirinha.html",
            {
                "request": request,
                "nome_social": registro.get("Nome_Social", ""),
                "identidade_genero": registro.get("Identidade_Genero", ""),
                "raca_etnia": registro.get("Raca_Etnia", ""),
            },
        )

    if status == "Reprovado":
        return templates.TemplateResponse(
            "mensagem.html",
            {
                "request": request,
                "tipo": "erro",
                "titulo": "Cadastro não aprovado",
                "mensagem": (
                    "Infelizmente o seu cadastro não foi aprovado. "
                    "Entre em contato com a associação para mais informações."
                ),
            },
        )

    # Pendente (ou qualquer outro status desconhecido)
    return templates.TemplateResponse(
        "mensagem.html",
        {
            "request": request,
            "tipo": "info",
            "titulo": "Cadastro em análise",
            "mensagem": (
                "Recebemos seu cadastro e ele está sendo analisado pela equipe. "
                "Aguarde o contato da associação."
            ),
        },
    )


# ── GET /cadastro — Exibe formulário de cadastro (dados opcionalmente pré-preenchidos) ─
@app.get("/cadastro", response_class=HTMLResponse)
async def formulario_cadastro(
    request: Request,
    cpf: str = "",
    data_nascimento: str = "",
) -> HTMLResponse:
    return templates.TemplateResponse(
        "cadastro.html",
        {
            "request": request,
            "cpf": cpf,
            "data_nascimento": data_nascimento,
        },
    )


# ── POST /cadastro — Salva novo cadastro ─────────────────────────────────────
@app.post("/cadastro", response_class=HTMLResponse)
async def salvar_cadastro(
    request: Request,
    cpf: Annotated[str, Form()],
    data_nascimento: Annotated[str, Form()],
    nome_social: Annotated[str, Form()],
    nome_registro: Annotated[str, Form()],
    identidade_genero: Annotated[str, Form()],
    raca_etnia: Annotated[str, Form()],
    inicio_transicao: Annotated[str, Form()] = "",
    pcd: Annotated[str, Form()] = "Não",
    rede_social: Annotated[str, Form()] = "",
    declaracao_verdade: Annotated[str, Form()] = "off",
) -> HTMLResponse:
    cpf_limpo = sanitize_cpf(cpf)

    # Regra de negócio: identidade cisgênero → reprovado direto
    status_inicial = "Reprovado" if _identidade_eh_cis(identidade_genero) else "Pendente"

    # Declaração de veracidade (checkbox)
    declaracao = "Sim" if declaracao_verdade in ("on", "true", "1", "yes") else "Não"

    nova_linha = [
        timestamp_br(),
        cpf_limpo,
        data_nascimento,
        nome_social,
        nome_registro,
        identidade_genero,
        raca_etnia,
        inicio_transicao,
        pcd,
        rede_social,
        declaracao,
        status_inicial,
    ]

    ws = get_worksheet(ABA_CADASTRO)
    ws.append_row(nova_linha, value_input_option="USER_ENTERED")

    return templates.TemplateResponse(
        "mensagem.html",
        {
            "request": request,
            "tipo": "sucesso",
            "titulo": "Cadastro recebido!",
            "mensagem": (
                f"Olá, {nome_social}! Seu cadastro foi enviado com sucesso "
                "e está aguardando análise da equipe. "
                "Você pode consultá-lo a qualquer momento pela tela inicial."
            ),
        },
    )


# ── GET /evento — Formulário de check-in ─────────────────────────────────────
@app.get("/evento", response_class=HTMLResponse)
async def formulario_evento(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("evento.html", {"request": request})


# ── POST /evento — Registra presença ─────────────────────────────────────────
@app.post("/evento", response_class=HTMLResponse)
async def registrar_evento(
    request: Request,
    nome_social: Annotated[str, Form()],
    aceite_imagem: Annotated[str, Form()] = "off",
) -> HTMLResponse:
    from datetime import timedelta

    brt = timezone(timedelta(hours=-3))
    data_hoje = datetime.now(tz=brt).strftime("%d-%m-%Y")
    titulo_aba = f"Evento_{data_hoje}"

    aceite = "Sim" if aceite_imagem in ("on", "true", "1", "yes") else "Não"

    ws = _garantir_aba_evento(titulo_aba)
    ws.append_row([timestamp_br(), nome_social, aceite], value_input_option="USER_ENTERED")

    return templates.TemplateResponse(
        "mensagem.html",
        {
            "request": request,
            "tipo": "sucesso",
            "titulo": "Check-in realizado!",
            "mensagem": f"Presença de {nome_social} registrada com sucesso. Boas-vindas ao evento!",
        },
    )
