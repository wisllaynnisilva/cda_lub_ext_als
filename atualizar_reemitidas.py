# -*- coding: utf-8 -*-
"""
atualizar_reemitidas.py — atualiza NA PRÓPRIA LINHA as amostras da planilha
de resultados que o laboratório reemitiu/corrigiu depois de gravadas.

A carga diária grava cada amostra uma única vez (resultDate = ontem). Quando
a ALS refaz uma análise ou corrige o cadastro, a data do resultado não muda e
a planilha ficaria com o valor antigo. Este script:

  1. busca na API todas as amostras (todo o histórico, ou só DIAS);
  2. compara, coluna a coluna, com a linha da planilha;
  3. guarda as linhas antigas das amostras alteradas numa aba
     'backup_reemitidas_AAAAMMDD' (no mesmo arquivo);
  4. regrava só as células que mudaram (mesmo formato da carga diária).

Não acrescenta nem remove linhas e não mexe no cabeçalho.
APLICAR=nao só mostra o que mudaria.
"""

import os
import time
from collections import Counter
from datetime import date, timedelta

from gspread.utils import rowcol_to_a1

import cda_lub_ext_als as als
from cda_utils import _norm_valor, _para_celula, abrir_aba, log, sheets_retry
from verificar_carga import buscar, norm

DIAS = [d.strip() for d in (os.getenv("DIAS") or "").split(",") if d.strip()]
ANO_INICIAL = int(os.getenv("ANO_INICIAL") or 2021)
APLICAR = (os.getenv("APLICAR") or "sim").strip().lower() in ("sim", "s", "true", "1", "yes")
LOTE = 500


def para_planilha(col, v):
    v = _para_celula(v)
    if col not in als._COLUNAS_DATA and isinstance(v, str) and als._PARECE_DATA.match(v):
        return "'" + v.strip()
    return v


def coletar_api():
    periodos = [(d, d) for d in DIAS]
    if not periodos:
        ontem = date.today() - timedelta(days=1)
        periodos = [(date(a, 1, 1).isoformat(), min(date(a, 12, 31), ontem).isoformat())
                    for a in range(ANO_INICIAL, ontem.year + 1)]
    api = {}
    for ini, fim in periodos:
        regs = buscar(ini, fim)
        log.info("API %s..%s: %d resultados", ini, fim, len(regs))
        for r in regs:
            api[_norm_valor(r.get("sampleNumber"))] = r
    return api


def main():
    als.login()
    api = coletar_api()

    # leitura da planilha só depois da API (minutos antes de gravar), para
    # que as posições das linhas estejam atualizadas
    aba = abrir_aba(als.PLANILHAS["results"])
    valores = sheets_retry(aba.get_all_values, value_render_option="UNFORMATTED_VALUE")
    header = [str(c).strip() for c in valores[0]]
    idx = {c: i for i, c in enumerate(header) if c}
    colunas = [c for c in als.ORDEM_COLUNAS if c in idx]
    i_num = idx["sampleNumber"]

    vistos, alteracoes, linhas_antigas = set(), [], []
    por_coluna, amostras = Counter(), []
    for r, linha in enumerate(valores[1:], start=2):
        n = _norm_valor(linha[i_num] if len(linha) > i_num else "")
        if not n or n in vistos or n not in api:
            continue
        vistos.add(n)
        esperado = api[n]
        mudou = False
        for col in colunas:
            i = idx[col]
            atual = linha[i] if i < len(linha) else ""
            novo = esperado.get(col)
            if norm(col, atual) == norm(col, novo):
                continue
            alteracoes.append({"range": rowcol_to_a1(r, i + 1),
                               "values": [[para_planilha(col, novo)]]})
            por_coluna[col] += 1
            mudou = True
        if mudou:
            amostras.append(n)
            linhas_antigas.append(list(linha) + [""] * (len(header) - len(linha)))

    log.info("%d amostras reemitidas, %d células a atualizar", len(amostras), len(alteracoes))

    if APLICAR and alteracoes:
        # o arquivo não pode ter mudado de tamanho desde a leitura (rotação)
        atual = sheets_retry(aba.col_values, i_num + 1, value_render_option="UNFORMATTED_VALUE")
        if len(atual) < len(valores):
            raise RuntimeError("A planilha encolheu durante a execução (rotação?); nada foi gravado")

        nome_bkp = f"backup_reemitidas_{date.today():%Y%m%d}"
        bkp = sheets_retry(aba.spreadsheet.add_worksheet, nome_bkp,
                           rows=len(linhas_antigas) + 1, cols=len(header))
        sheets_retry(bkp.update, [header] + [[_para_celula(v) for v in l] for l in linhas_antigas],
                     "A1", value_input_option="RAW")
        log.info("Linhas antigas guardadas na aba '%s'", nome_bkp)

        for i in range(0, len(alteracoes), LOTE):
            sheets_retry(aba.batch_update, alteracoes[i:i + LOTE], value_input_option="USER_ENTERED")
        log.info("%d células atualizadas", len(alteracoes))

    t = [
        "## Atualização das amostras reemitidas — ALS results",
        "",
        f"Modo: {'APLICADO' if APLICAR else 'simulação (nada gravado)'}",
        "",
        "| | quantidade |",
        "|---|---|",
        f"| Amostras conferidas | {len(vistos)} |",
        f"| Amostras atualizadas | {len(amostras)} |",
        f"| Células atualizadas | {len(alteracoes)} |",
        "",
    ]
    if por_coluna:
        t += ["### Células por coluna", ""] + [f"- {c}: {q}" for c, q in por_coluna.most_common()] + [""]
        t += ["### Amostras", "", ", ".join(amostras[:1000]), ""]
    texto = "\n".join(t)
    print(texto)
    resumo = os.getenv("GITHUB_STEP_SUMMARY")
    if resumo:
        with open(resumo, "a", encoding="utf-8") as f:
            f.write(texto + "\n")


if __name__ == "__main__":
    inicio = time.time()
    main()
    log.info("Concluído em %.0f min", (time.time() - inicio) / 60)
