# -*- coding: utf-8 -*-
"""
revisar_historico.py — revisão SOMENTE LEITURA do histórico da ALS.

Busca na API todos os resultados liberados (ano a ano) e compara com a
planilha de resultados. Não grava nada em nenhuma planilha.

Mostra:
  * amostras que existem na API e FALTAM na planilha (por mês e a lista);
  * amostras que estão na planilha e não vieram da API;
  * amostras cuja data de resultado na API difere da planilha
    (resultado reemitido/atualizado depois de gravado).

Para incluir na planilha o que faltar, depois rode o workflow diário
manualmente com data_referencia = cada dia indicado no relatório.
"""

import os
import time
from collections import Counter
from datetime import date, timedelta

import cda_lub_ext_als as als
from cda_utils import _norm_data, _norm_valor, abrir_aba, log, sheets_retry

ANO_INICIAL = int(os.getenv("ANO_INICIAL") or 2015)
POR_PAGINA = [100]   # se a API recusar 100, cai para 50 (o usado na carga diária)


def buscar_periodo(inicio, fim):
    """Todos os resultados com resultDate entre inicio e fim (paginação por offset)."""
    regs, offset = [], 0
    while True:
        corpo = {
            "sinceResultDate": inicio.isoformat(),
            "untilResultDate": fim.isoformat(),
            "offset": offset,
            "max": POR_PAGINA[0],
            "order": "resultDate",
            "sort": "desc",
        }
        try:
            dados = als.api_post(als.URL_SAMPLERESULTS, corpo)
        except Exception as exc:  # noqa: BLE001
            r = getattr(exc, "response", None)
            if POR_PAGINA[0] != 50 and r is not None and 400 <= r.status_code < 500:
                log.warning("API recusou max=%d; usando 50", POR_PAGINA[0])
                POR_PAGINA[0] = 50
                continue
            raise
        pagina = dados.get("results") or []
        if not pagina:
            return regs
        regs.extend(
            {"sampleNumber": _norm_valor(r.get("sampleNumber")),
             "resultDate": _norm_data(r.get("resultDate"))[:10]}
            for r in pagina
        )
        offset += len(pagina)   # funciona qualquer que seja o máximo aceito pela API
        time.sleep(als.PAUSA_ENTRE_PAGINAS)


def main():
    als.login()
    ontem = date.today() - timedelta(days=1)

    api = {}
    for ano in range(ANO_INICIAL, ontem.year + 1):
        ini, fim = date(ano, 1, 1), min(date(ano, 12, 31), ontem)
        regs = buscar_periodo(ini, fim)
        log.info("API %d: %d resultados", ano, len(regs))
        for r in regs:
            atual = api.get(r["sampleNumber"])
            if atual is None or r["resultDate"] > atual:
                api[r["sampleNumber"]] = r["resultDate"]

    aba = abrir_aba(als.PLANILHAS["results"])
    valores = sheets_retry(aba.get_all_values, value_render_option="UNFORMATTED_VALUE")
    header = [str(c).strip() for c in valores[0]]
    i_num, i_dt = header.index("sampleNumber"), header.index("resultDate")
    planilha = {}
    for l in valores[1:]:
        l = list(l) + [""] * (len(header) - len(l))
        n = _norm_valor(l[i_num])
        if n:
            planilha[n] = _norm_data(l[i_dt])[:10]

    faltando = sorted((api[n], n) for n in api.keys() - planilha.keys())
    so_planilha = sorted(planilha.keys() - api.keys())
    data_diferente = sorted(
        (n, planilha[n], api[n]) for n in api.keys() & planilha.keys()
        if planilha[n] and api[n] and planilha[n] != api[n]
    )
    por_mes = Counter(d[:7] for d, _ in faltando)
    dias = sorted({d for d, _ in faltando})

    linhas = [
        "## Revisão do histórico ALS (somente leitura)",
        "",
        "| | quantidade |",
        "|---|---|",
        f"| Resultados na API ({ANO_INICIAL} até {ontem}) | {len(api)} |",
        f"| Amostras na planilha | {len(planilha)} |",
        f"| **Na API e faltando na planilha** | **{len(faltando)}** |",
        f"| Na planilha e não encontradas na API | {len(so_planilha)} |",
        f"| Com data de resultado diferente (reemitidas) | {len(data_diferente)} |",
        "",
    ]
    if faltando:
        linhas += ["### Faltando por mês", "", "| mês | amostras |", "|---|---|"]
        linhas += [f"| {m} | {q} |" for m, q in sorted(por_mes.items())]
        linhas += ["", f"### Dias a reprocessar ({len(dias)})", "", ", ".join(dias), "",
                   "### Amostras faltando (resultDate, sampleNumber)", "",
                   ", ".join(f"{d} {n}" for d, n in faltando[:2000]), ""]
    if data_diferente:
        linhas += ["### Data diferente (sampleNumber: planilha → API)", "",
                   ", ".join(f"{n}: {p}→{a}" for n, p, a in data_diferente[:500]), ""]
    if so_planilha:
        linhas += ["### Só na planilha (sampleNumber)", "", ", ".join(so_planilha[:500]), ""]

    texto = "\n".join(linhas)
    print(texto)
    resumo = os.getenv("GITHUB_STEP_SUMMARY")
    if resumo:
        with open(resumo, "a", encoding="utf-8") as f:
            f.write(texto + "\n")


if __name__ == "__main__":
    inicio = time.time()
    main()
    log.info("Revisão concluída em %.0f min", (time.time() - inicio) / 60)
