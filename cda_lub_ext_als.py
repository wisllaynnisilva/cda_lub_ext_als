# -*- coding: utf-8 -*-
"""
cda_lub_ext_als — coleta do laboratório ALS (análise de lubrificantes) e
carga no Google Sheets.

Etapas (isoladas):
  status   — status de todas as amostras (substitui a aba);
  results  — resultados liberados no dia de referência (padrão D-1),
             anexados sem duplicar sampleNumber.
"""

from concurrent.futures import ThreadPoolExecutor
import time

import pandas as pd

from cda_utils import (
    Pipeline, anexar_novos, dia_referencia, env_obrigatoria, gravar_aba, http, log,
)

# ---------------------------------------------------------------------------
# 1. CONFIGURAÇÃO
# ---------------------------------------------------------------------------

CREDENCIAIS = {
    "username": env_obrigatoria("ALS_USERNAME"),
    "password": env_obrigatoria("ALS_PASSWORD"),
}
URL_BASE = env_obrigatoria("ALS_URL").rstrip("/")
URL_LOGIN = f"{URL_BASE}/api/login"
URL_SAMPLELIST = f"{URL_BASE}/api/v1/amostra/list"
URL_SAMPLERESULTS = f"{URL_BASE}/api/v1/sampleResult/search"

PAUSA_ENTRE_PAGINAS = 25   # segundos (limite de requisições da ALS)

PLANILHAS = {
    "status": "1kYOyTVTbv8WHobnhh56F6QnW4U127ti8EiLi-TPfSiA",
    "results": "1aSpmZ10Z_xECpfFM0oceQcJ47MLrhz4RKoizOt7QwDk",
}

COLUNAS_BASE = [
    "sampleNumber", "evaluation", "inspectionAction", "resultStatus",
    "resultDate", "receiptDate", "registrationDate", "dateSampled",
    "equipmentSite", "equipmentArea", "equipmentSector", "equipmentTag",
    "equipmentFamily", "equipmentMaker", "equipmentModel", "compartmentName",
    "compartmentTypeName", "oilType", "oilViscosity",
]

TESTES = [
    "Visual", "PQ_Index", "Oxidação-FTIR", "Viscosidade_40°C",
    "KF_Coulométrico", "TAN_Colorimétrico", "Cromo", "Cobre", "Vanádio",
    "Potássio", "Estanho", "Prata", "Molibdênio", "Sódio", "Zinco",
    "Alumínio", "Manganês", "Silício", "Magnésio", "Ferro", "Níquel",
    "Cádmio", "Bário", "Fósforo", "Cálcio", "Chumbo", "Boro", "Titânio",
    "Classe_ISO", "SAE_4µm", "SAE_6µm", "SAE_14µm", "SAE_21µm", "SAE_38µm",
    "SAE_70µm", ">4", ">6", ">14",
]
SUFIXOS = ["testId", "testName", "testType", "testMethod", "testresultsId", "value", "unit", "status"]

# mesma ordem de colunas de antes (19 colunas base + 8 por teste)
ORDEM_COLUNAS = COLUNAS_BASE + [f"{t}_{s}" for t in TESTES for s in SUFIXOS]

# ---------------------------------------------------------------------------
# 2. AUTENTICAÇÃO
# ---------------------------------------------------------------------------

_headers = {}


def login():
    resp = http("POST", URL_LOGIN, json=CREDENCIAIS)
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError("Login ALS sem access_token")
    _headers.clear()
    _headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    log.info("Login ALS ok")   # o token NÃO é mais impresso no log
    return dict(_headers)


def api_post(url, corpo):
    return http(
        "POST", url,
        headers=dict(_headers) or login(),
        json=corpo,
        ao_401=login,
    ).json()


# ---------------------------------------------------------------------------
# 3. STATUS DE AMOSTRAS
# ---------------------------------------------------------------------------

SITUACOES = ["EM_PROCESSAMENTO", "COLETADA", "SEGREGADA", "FINALIZADA"]


def _pagina_status(pagina):
    return api_post(URL_SAMPLELIST, {
        "maximoPorPagina": 100,
        "numeroPagina": pagina,
        "situacao": SITUACOES,
    })


def etapa_status():
    primeira = _pagina_status(1)
    total_paginas = int(primeira.get("totalPaginas") or 1)
    resultados = list(primeira.get("resultados") or [])

    # demais páginas em paralelo; erro em qualquer página aborta a etapa
    # (antes a página com erro virava lista vazia e a planilha ficava incompleta)
    with ThreadPoolExecutor(max_workers=5) as pool:
        for pagina in pool.map(_pagina_status, range(2, total_paginas + 1)):
            resultados.extend(pagina.get("resultados") or [])

    log.info("Status: %d amostras em %d páginas", len(resultados), total_paginas)
    df = pd.json_normalize(resultados, sep="_", max_level=2)
    gravar_aba(PLANILHAS["status"], df, "status")
    return df


# ---------------------------------------------------------------------------
# 4. RESULTADOS DE AMOSTRAS
# ---------------------------------------------------------------------------

def processar_linha(linha):
    valid = linha.get("validResult") or {}
    equipment = linha.get("equipment") or {}
    coleta = linha.get("collectionData") or {}
    oil = coleta.get("oil") or {}
    compartment = linha.get("compartment") or {}

    base = {
        "sampleNumber": linha.get("sampleNumber"),
        "evaluation": valid.get("evaluation"),
        "inspectionAction": valid.get("inspectionAction"),
        "resultStatus": valid.get("resultStatus") or "sampleResultsStatus",
        "equipmentTag": equipment.get("tag"),
        "equipmentFamily": (equipment.get("family") or {}).get("name"),
        "equipmentMaker": (equipment.get("maker") or {}).get("name"),
        "equipmentModel": equipment.get("model"),
        "equipmentSite": (equipment.get("site") or {}).get("name"),
        "equipmentArea": equipment.get("area"),
        "equipmentSector": equipment.get("sector"),
        "compartmentName": compartment.get("name"),
        "compartmentTypeName": (compartment.get("type") or {}).get("name"),
        "registrationDate": coleta.get("registrationDate"),
        "dateSampled": coleta.get("dateSampled"),
        "oilViscosity": (oil.get("viscosity") or {}).get("name"),
        "oilType": (oil.get("manufacturer") or {}).get("name"),
        "receiptDate": linha.get("receiptDate"),
        "resultDate": linha.get("resultDate"),
    }

    # Cada teste vira colunas: {teste}_value, {teste}_status, {teste}_unit, ...
    for tr in linha.get("testResults") or []:
        test = tr.get("test") or {}
        traducao = test.get("translation") or {}
        nome = str(traducao.get("name") or test.get("id", "unknown"))
        col = nome.strip().replace(" ", "_").replace("/", "_")

        base[f"{col}_value"] = tr.get("resultValue")
        base[f"{col}_status"] = tr.get("resultStatus")
        base[f"{col}_unit"] = traducao.get("unitOfMeasure")
        base[f"{col}_testresultsId"] = tr.get("id")
        base[f"{col}_testId"] = test.get("id")
        base[f"{col}_testType"] = traducao.get("name")
        base[f"{col}_testMethod"] = traducao.get("method")
        base[f"{col}_testName"] = (test.get("testGroup") or {}).get("name")
    return base


def etapa_results():
    dia = dia_referencia().isoformat()
    log.info("Resultados de %s", dia)

    registros, offset = [], 0
    while True:
        dados = api_post(URL_SAMPLERESULTS, {
            "sinceResultDate": dia,
            "untilResultDate": dia,
            "offset": offset,
            "max": 50,
            "order": "resultDate",
            "sort": "desc",
        })
        pagina = dados.get("results") or []
        log.info("offset %d: %d resultados", offset, len(pagina))
        if not pagina:
            break
        registros.extend(processar_linha(item) for item in pagina)
        offset += 50
        time.sleep(PAUSA_ENTRE_PAGINAS)

    if not registros:
        log.info("Nenhum resultado novo para %s", dia)
        return pd.DataFrame()

    df = pd.DataFrame(registros)
    desconhecidas = sorted(set(df.columns) - set(ORDEM_COLUNAS))
    if desconhecidas:
        log.warning("Testes novos fora da lista de colunas (ignorados): %s", desconhecidas)
    df = df.reindex(columns=ORDEM_COLUNAS)
    df["sampleNumber"] = df["sampleNumber"].astype(str)

    # Antes: se o cabeçalho da planilha fosse diferente, a aba era APAGADA e
    # ficava só com os dados de ontem. Agora as linhas são alinhadas pelo
    # cabeçalho existente e apenas sampleNumber novos são anexados.
    anexar_novos(PLANILHAS["results"], df, chaves=["sampleNumber"], nome="results")
    return df


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    p = Pipeline("cda_lub_ext_als")
    p.etapa("login", login)
    p.etapa("status", etapa_status, depende_de=["login"])
    p.etapa("results", etapa_results, depende_de=["login"])
    p.finalizar()
