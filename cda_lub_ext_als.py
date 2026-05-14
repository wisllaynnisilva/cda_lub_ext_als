# -*- coding: utf-8 -*-
"""cda_lub_ext_als.ipynb

#**1. BIBLIOTECAS**
"""

import json
import time
import requests
import datetime
import pandas as pd
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import gspread
from google.oauth2.service_account import Credentials
from gspread_dataframe import get_as_dataframe, set_with_dataframe

"""# **2. DADOS DE ACESSO**

##**2.1. Credenciais**
"""

credentials = {
        "username": os.getenv("ALS_USERNAME"),
        "password": os.getenv("ALS_PASSWORD")
    }

"""## **2.2. URL's**"""

url_base = os.getenv("ALS_URL")
url_login = url_base + "/api/login"
url_samplelist = url_base + "/api/v1/amostra/list"
url_sampleresults = url_base + "/api/v1/sampleResult/search"

"""##**2.3. Token**"""

try:
    url = url_login

    hdr = {
        'Content-Type': 'application/json',
    }

    credentials = credentials

    data = json.dumps(credentials)
    req = urllib.request.Request(url, headers=hdr, data=data.encode("utf-8"), method='POST')

    with urllib.request.urlopen(req) as response:
        response_data = response.read().decode("utf-8")
        response_json = json.loads(response_data)

        access_token = response_json.get("access_token")

        print("Token:", access_token)

except Exception as e:
    print("Erro:", e)

"""##**2.4. Header**"""

header = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

"""##**2.5. Sheets**

###**2.5.1. Autenticação no Sheets**
"""

# Lê a variável de ambiente com o conteúdo do JSON da conta de serviço
service_account_info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"])

# Define os escopos de acesso (Google Sheets)
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

# Cria as credenciais usando o conteúdo do secret
creds = Credentials.from_service_account_info(service_account_info, scopes=SCOPES)

# Autentica no Google Sheets
gc = gspread.authorize(creds)

"""#**3. STATUS DE AMOSTRAS**

##**3.1. Execução**
"""

url = url_samplelist
hdr = header

# Função para buscar uma página
def fetch_pagina(pagina):
    try:
        filters = {
            "maximoPorPagina": 100,
            "numeroPagina": pagina,
            "situacao": ["EM_PROCESSAMENTO", "COLETADA", "SEGREGADA", "FINALIZADA"]
        }
        data = json.dumps(filters).encode("utf-8")
        req = urllib.request.Request(url, headers=hdr, data=data)
        req.get_method = lambda: 'POST'
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode('utf-8'))
            return result.get("resultados", [])
    except Exception as e:
        print(f"Erro na página {pagina}:", e)
        return []

try:
    filters = {
        "maximoPorPagina": 100,
        "numeroPagina": 1,
        "situacao": ["EM_PROCESSAMENTO", "COLETADA", "SEGREGADA", "FINALIZADA"]
    }
    data = json.dumps(filters).encode("utf-8")
    req = urllib.request.Request(url, headers=hdr, data=data)
    req.get_method = lambda: 'POST'
    with urllib.request.urlopen(req) as response:
        primeira_resposta = json.loads(response.read().decode('utf-8'))
        total_paginas = primeira_resposta.get("totalPaginas", 1)
        resultados_totais = primeira_resposta.get("resultados", [])

    # Buscar as páginas restantes em paralelo
    paginas_restantes = list(range(2, total_paginas + 1))
    with ThreadPoolExecutor(max_workers=5) as executor:
        resultados = executor.map(fetch_pagina, paginas_restantes)

    for pagina_resultado in resultados:
        resultados_totais.extend(pagina_resultado)

    print(f"Total de resultados: {len(resultados_totais)}")
    print(json.dumps(resultados_totais, indent=2))

except Exception as e:
    print("Erro:", e)

"""##**3.2.DataFrame**"""

# Transforma os resultados em um DataFrame expandindo os campos aninhados
df = pd.json_normalize(
    resultados_totais,
    sep='_',  # para gerar colunas como cliente_nome, obra_nome etc.
    max_level=2  # expande até 2 níveis; aumente se necessário
)

# Visualiza as colunas
# print("Colunas disponíveis:", df.columns.tolist())

tb_status = pd.DataFrame(df)

"""##**3.3.Carga no Sheets**"""

# Nome da planilha
planilha_id = "1kYOyTVTbv8WHobnhh56F6QnW4U127ti8EiLi-TPfSiA"
nome_da_aba = "Sheet1"

# Abre a planilha e a aba
planilha = gc.open_by_key(planilha_id)
aba = planilha.worksheet(nome_da_aba)

# Limpa a aba antes de escrever os dados
aba.clear()

# Envia o DataFrame para a aba
set_with_dataframe(aba, tb_status)

print("Dados enviados com sucesso para o Google Sheets!")

"""#**4. RESULTADO DE AMOSTRAS**

##**4.1. Execução**
"""

# Função para fazer a requisição paginada
def buscar_dados(offset):
    url = url_sampleresults
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    ontem = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()


    filters = {
        "sinceResultDate": ontem,
        "untilResultDate": ontem,
        "offset": offset,
        "max": 50,
        "order": "resultDate",
        "sort": "desc"
    }
    data = json.dumps(filters).encode("utf-8")
    req = urllib.request.Request(url, headers=headers, data=data)
    req.get_method = lambda: 'POST'

    try:
        response = urllib.request.urlopen(req)
        result = json.loads(response.read())
        return result.get("results", [])
    except Exception as e:
        print("Erro ao requisitar dados:", e)
        return []

# Função de transformação para expandir testResults
def processar_linha(linha):
    validResult = linha.get("validResult") or {}
    equipment = linha.get("equipment") or {}
    collectionData = linha.get("collectionData") or {}
    oil = collectionData.get("oil") or {}
    compartment = linha.get("compartment") or {}
    site = equipment.get("site") or {}
    family = equipment.get("family") or {}
    maker = equipment.get("maker") or {}
    oil_viscosity = oil.get("viscosity") or {}
    oil_manufacturer = oil.get("manufacturer") or {}
    compartment_type = compartment.get("type") or {}

    base = {
        "sampleNumber": linha.get("sampleNumber"),
        "evaluation": validResult.get("evaluation"),
        "inspectionAction": validResult.get("inspectionAction"),
        "resultStatus": validResult.get("resultStatus") or "sampleResultsStatus",
        "equipmentTag": equipment.get("tag"),
        "equipmentFamily": family.get("name"),
        "equipmentMaker": maker.get("name"),
        "equipmentModel": equipment.get("model"),
        "equipmentSite": site.get("name"),
        "equipmentArea": equipment.get("area"),
        "equipmentSector": equipment.get("sector"),
        "compartmentName": compartment.get("name"),
        "compartmentTypeName": compartment_type.get("name"),
        "registrationDate": collectionData.get("registrationDate"),
        "dateSampled": collectionData.get("dateSampled"),
        "oilViscosity": oil_viscosity.get("name"),
        "oilType": oil_manufacturer.get("name"),
        "receiptDate": linha.get("receiptDate"),
        "resultDate": linha.get("resultDate")
    }

    # ── Pivota testResults em colunas ────────────────────────────────────────
    # Cada teste vira: {testName_value, testName_status, testName_unit}
    for tr in (linha.get("testResults") or []):
      test        = tr.get("test", {}) or {}
      translation = test.get("translation") or {}
      test_group  = test.get("testGroup", {}) or {}
      test_name   = translation.get("name") or test.get("id", "unknown")
      col         = test_name.strip().replace(" ", "_").replace("/", "_")

      base[f"{col}_value"]         = tr.get("resultValue")
      base[f"{col}_status"]        = tr.get("resultStatus")
      base[f"{col}_unit"]          = translation.get("unitOfMeasure")
      base[f"{col}_testresultsId"] = tr.get("id")           # id do resultado
      base[f"{col}_testId"]        = test.get("id")         # id do teste
      base[f"{col}_testType"]      = translation.get("name")
      base[f"{col}_testMethod"]    = translation.get("method")
      base[f"{col}_testName"]      = test_group.get("name")

    return base


# ── Paginação ────────────────────────────────────────────────────────────────
offset = 0
todos_registros = []

while True:
    print(f"Buscando offset {offset}...")
    resultados = buscar_dados(offset)
    if not resultados:
        break

    for item in resultados:
        todos_registros.append(processar_linha(item))

    offset += 50
    time.sleep(25)

"""##**4.2. DataFrame**"""

ordem_colunas = [
"sampleNumber",
"evaluation",
"inspectionAction",
"resultStatus",
"resultDate",
"receiptDate",
"registrationDate",
"dateSampled",
"equipmentSite",
"equipmentArea",
"equipmentSector",
"equipmentTag",
"equipmentFamily",
"equipmentMaker",
"equipmentModel",
"compartmentName",
"compartmentTypeName",
"oilType",
"oilViscosity",

"Visual_testId",
"Visual_testName",
"Visual_testType",
"Visual_testMethod",
"Visual_testresultsId",
"Visual_value",
"Visual_unit",
"Visual_status",

"PQ_Index_testId",
"PQ_Index_testName",
"PQ_Index_testType",
"PQ_Index_testMethod",
"PQ_Index_testresultsId",
"PQ_Index_value",
"PQ_Index_unit",
"PQ_Index_status",

"Oxidação-FTIR_testId",
"Oxidação-FTIR_testName",
"Oxidação-FTIR_testType",
"Oxidação-FTIR_testMethod",
"Oxidação-FTIR_testresultsId",
"Oxidação-FTIR_value",
"Oxidação-FTIR_unit",
"Oxidação-FTIR_status",

"Viscosidade_40°C_testId",
"Viscosidade_40°C_testName",
"Viscosidade_40°C_testType",
"Viscosidade_40°C_testMethod",
"Viscosidade_40°C_testresultsId",
"Viscosidade_40°C_value",
"Viscosidade_40°C_unit",
"Viscosidade_40°C_status",

"KF_Coulométrico_testId",
"KF_Coulométrico_testName",
"KF_Coulométrico_testType",
"KF_Coulométrico_testMethod",
"KF_Coulométrico_testresultsId",
"KF_Coulométrico_value",
"KF_Coulométrico_unit",
"KF_Coulométrico_status",

"TAN_Colorimétrico_testId",
"TAN_Colorimétrico_testName",
"TAN_Colorimétrico_testType",
"TAN_Colorimétrico_testMethod",
"TAN_Colorimétrico_testresultsId",
"TAN_Colorimétrico_value",
"TAN_Colorimétrico_unit",
"TAN_Colorimétrico_status",

"Cromo_testId",
"Cromo_testName",
"Cromo_testType",
"Cromo_testMethod",
"Cromo_testresultsId",
"Cromo_value",
"Cromo_unit",
"Cromo_status",

"Cobre_testId",
"Cobre_testName",
"Cobre_testType",
"Cobre_testMethod",
"Cobre_testresultsId",
"Cobre_value",
"Cobre_unit",
"Cobre_status",

"Vanádio_testId",
"Vanádio_testName",
"Vanádio_testType",
"Vanádio_testMethod",
"Vanádio_testresultsId",
"Vanádio_value",
"Vanádio_unit",
"Vanádio_status",

"Potássio_testId",
"Potássio_testName",
"Potássio_testType",
"Potássio_testMethod",
"Potássio_testresultsId",
"Potássio_value",
"Potássio_unit",
"Potássio_status",

"Estanho_testId",
"Estanho_testName",
"Estanho_testType",
"Estanho_testMethod",
"Estanho_testresultsId",
"Estanho_value",
"Estanho_unit",
"Estanho_status",

"Prata_testId",
"Prata_testName",
"Prata_testType",
"Prata_testMethod",
"Prata_testresultsId",
"Prata_value",
"Prata_unit",
"Prata_status",

"Molibdênio_testId",
"Molibdênio_testName",
"Molibdênio_testType",
"Molibdênio_testMethod",
"Molibdênio_testresultsId",
"Molibdênio_value",
"Molibdênio_unit",
"Molibdênio_status",

"Sódio_testId",
"Sódio_testName",
"Sódio_testType",
"Sódio_testMethod",
"Sódio_testresultsId",
"Sódio_value",
"Sódio_unit",
"Sódio_status",

"Zinco_testId",
"Zinco_testName",
"Zinco_testType",
"Zinco_testMethod",
"Zinco_testresultsId",
"Zinco_value",
"Zinco_unit",
"Zinco_status",

"Alumínio_testId",
"Alumínio_testName",
"Alumínio_testType",
"Alumínio_testMethod",
"Alumínio_testresultsId",
"Alumínio_value",
"Alumínio_unit",
"Alumínio_status",

"Manganês_testId",
"Manganês_testName",
"Manganês_testType",
"Manganês_testMethod",
"Manganês_testresultsId",
"Manganês_value",
"Manganês_unit",
"Manganês_status",

"Silício_testId",
"Silício_testName",
"Silício_testType",
"Silício_testMethod",
"Silício_testresultsId",
"Silício_value",
"Silício_unit",
"Silício_status",

"Magnésio_testId",
"Magnésio_testName",
"Magnésio_testType",
"Magnésio_testMethod",
"Magnésio_testresultsId",
"Magnésio_value",
"Magnésio_unit",
"Magnésio_status",

"Ferro_testId",
"Ferro_testName",
"Ferro_testType",
"Ferro_testMethod",
"Ferro_testresultsId",
"Ferro_value",
"Ferro_unit",
"Ferro_status",

"Níquel_testId",
"Níquel_testName",
"Níquel_testType",
"Níquel_testMethod",
"Níquel_testresultsId",
"Níquel_value",
"Níquel_unit",
"Níquel_status",

"Cádmio_testId",
"Cádmio_testName",
"Cádmio_testType",
"Cádmio_testMethod",
"Cádmio_testresultsId",
"Cádmio_value",
"Cádmio_unit",
"Cádmio_status",

"Bário_testId",
"Bário_testName",
"Bário_testType",
"Bário_testMethod",
"Bário_testresultsId",
"Bário_value",
"Bário_unit",
"Bário_status",

"Fósforo_testId",
"Fósforo_testName",
"Fósforo_testType",
"Fósforo_testMethod",
"Fósforo_testresultsId",
"Fósforo_value",
"Fósforo_unit",
"Fósforo_status",

"Cálcio_testId",
"Cálcio_testName",
"Cálcio_testType",
"Cálcio_testMethod",
"Cálcio_testresultsId",
"Cálcio_value",
"Cálcio_unit",
"Cálcio_status",

"Chumbo_testId",
"Chumbo_testName",
"Chumbo_testType",
"Chumbo_testMethod",
"Chumbo_testresultsId",
"Chumbo_value",
"Chumbo_unit",
"Chumbo_status",

"Boro_testId",
"Boro_testName",
"Boro_testType",
"Boro_testMethod",
"Boro_testresultsId",
"Boro_value",
"Boro_unit",
"Boro_status",

"Titânio_testId",
"Titânio_testName",
"Titânio_testType",
"Titânio_testMethod",
"Titânio_testresultsId",
"Titânio_value",
"Titânio_unit",
"Titânio_status",

"Classe_ISO_testId",
"Classe_ISO_testName",
"Classe_ISO_testType",
"Classe_ISO_testMethod",
"Classe_ISO_testresultsId",
"Classe_ISO_value",
"Classe_ISO_unit",
"Classe_ISO_status",

"SAE_4µm_testId",
"SAE_4µm_testName",
"SAE_4µm_testType",
"SAE_4µm_testMethod",
"SAE_4µm_testresultsId",
"SAE_4µm_value",
"SAE_4µm_unit",
"SAE_4µm_status",

"SAE_6µm_testId",
"SAE_6µm_testName",
"SAE_6µm_testType",
"SAE_6µm_testMethod",
"SAE_6µm_testresultsId",
"SAE_6µm_value",
"SAE_6µm_unit",
"SAE_6µm_status",

"SAE_14µm_testId",
"SAE_14µm_testName",
"SAE_14µm_testType",
"SAE_14µm_testMethod",
"SAE_14µm_testresultsId",
"SAE_14µm_value",
"SAE_14µm_unit",
"SAE_14µm_status",

"SAE_21µm_testId",
"SAE_21µm_testName",
"SAE_21µm_testType",
"SAE_21µm_testMethod",
"SAE_21µm_testresultsId",
"SAE_21µm_value",
"SAE_21µm_unit",
"SAE_21µm_status",

"SAE_38µm_testId",
"SAE_38µm_testName",
"SAE_38µm_testType",
"SAE_38µm_testMethod",
"SAE_38µm_testresultsId",
"SAE_38µm_value",
"SAE_38µm_unit",
"SAE_38µm_status",

"SAE_70µm_testId",
"SAE_70µm_testName",
"SAE_70µm_testType",
"SAE_70µm_testMethod",
"SAE_70µm_testresultsId",
"SAE_70µm_value",
"SAE_70µm_unit",
"SAE_70µm_status",

">4_testId",
">4_testName",
">4_testType",
">4_testMethod",
">4_testresultsId",
">4_value",
">4_unit",
">4_status",

">6_testId",
">6_testName",
">6_testType",
">6_testMethod",
">6_testresultsId",
">6_value",
">6_unit",
">6_status",

">14_testId",
">14_testName",
">14_testType",
">14_testMethod",
">14_testresultsId",
">14_value",
">14_unit",
">14_status"
]

tb_results = pd.DataFrame(todos_registros)

tb_results = tb_results.reindex(columns=ordem_colunas)

"""##**4.3. Carga no Sheets**"""

# Nome da planilha e aba
planilha_id = "1aSpmZ10Z_xECpfFM0oceQcJ47MLrhz4RKoizOt7QwDk"
nome_da_aba = "Sheet1"

# Se a busca não retornar dados, encerra o processo de envio
if tb_results.empty:
    print("Nenhum dado novo encontrado na API para ontem. Nada a adicionar.")

else:
    # Abre a planilha
    planilha = gc.open_by_key(planilha_id)
    aba = planilha.worksheet(nome_da_aba)

    # Lê os dados atuais da planilha como DataFrame
    df_existente = get_as_dataframe(
        aba,
        evaluate_formulas=True,
        dtype=str
    ).dropna(how="all")

    # Normaliza nomes de colunas (evita espaço invisível no header)
    if not df_existente.empty:
        df_existente.columns = df_existente.columns.str.strip()

    # Se não existir a coluna sampleNumber ainda, cria estrutura mínima
    if df_existente.empty or "sampleNumber" not in df_existente.columns:
        df_existente = pd.DataFrame(columns=["sampleNumber"])

    else:
        df_existente = df_existente.dropna(subset=["sampleNumber"])

    # Garante que sampleNumber seja string para comparação
    tb_results["sampleNumber"] = tb_results["sampleNumber"].astype(str)
    df_existente["sampleNumber"] = df_existente["sampleNumber"].astype(str)

    # Sincroniza header da planilha se houver diferença de colunas
    colunas_planilha = list(df_existente.columns)
    colunas_df = list(tb_results.columns)

    if colunas_planilha != colunas_df:
        print("Estrutura da planilha desatualizada — recriando header...")
        aba.clear()
        set_with_dataframe(
            aba,
            tb_results,
            include_index=False,
            include_column_header=True,
            resize=True
        )
        print("Planilha sincronizada com sucesso.")

    else:
        # Filtra apenas os novos registros
        novos_registros = tb_results[
            ~tb_results["sampleNumber"].isin(df_existente["sampleNumber"])
        ]

        # Se houver novos dados, adiciona ao final da aba
        if not novos_registros.empty:
            proxima_linha = len(df_existente) + 2  # +1 header, +1 próxima linha

            set_with_dataframe(
                aba,
                novos_registros,
                row=proxima_linha,
                include_column_header=False
            )

            print(f"{len(novos_registros)} novas linhas adicionadas ao Google Sheets!")
        else:
            print("Nenhuma nova linha para adicionar — todos os dados já estão presentes.")
