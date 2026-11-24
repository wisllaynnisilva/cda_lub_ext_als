# -*- coding: utf-8 -*-
"""
verificar_carga.py — conferência SOMENTE LEITURA, coluna a coluna, da
planilha de resultados da ALS contra a API.

Para cada amostra devolvida pela API (nos dias de DIAS, ou em todo o
histórico desde ANO_INICIAL se DIAS estiver vazio), monta a linha esperada
com a mesma função da carga diária e compara com a linha da planilha:

  * faltando   — amostra da API que não está na planilha;
  * vazia      — a API tem valor e a célula da planilha está vazia;
  * diferente  — os dois têm valor, mas não batem;
  * testes fora da lista de colunas (dados que a carga não tem onde gravar).

Não grava nada em nenhuma planilha.
"""

import os
import re
import time
from collections import Counter, defaultdict
from datetime import date, timedelta

import cda_lub_ext_als as als
from cda_utils import _norm_data, _norm_valor, abrir_aba, log, sheets_retry

DIAS = [d.strip() for d in (os.getenv("DIAS") or "").split(",") if d.strip()]
ANO_INICIAL = int(os.getenv("ANO_INICIAL") or 2021)
COLUNAS_DATA = {"resultDate", "receiptDate", "registrationDate", "dateSampled"}


def buscar(inicio, fim):
    regs, offset = [], 0
    while True:
        dados = als.api_post(als.URL_SAMPLERESULTS, {
            "sinceResultDate": inicio, "untilResultDate": fim,
            "offset": offset, "max": 50, "order": "resultDate", "sort": "desc",
        })
        pagina = dados.get("results") or []
        if not pagina:
            return regs
        regs.extend(als.processar_linha(r) for r in pagina)
        offset += len(pagina)
        time.sleep(als.PAUSA_ENTRE_PAGINAS)


def norm(col, v):
    if col in COLUNAS_DATA:
        return _norm_data(v)
    s = _norm_valor(v)
    # "0,5" (planilha pt-BR) x "0.5" (API)
    if re.fullmatch(r"-?\d+,\d+", s):
        s = _norm_valor(float(s.replace(",", ".")))
    return s


def main():
    als.login()
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

    aba = abrir_aba(als.PLANILHAS["results"])
    valores = sheets_retry(aba.get_all_values, value_render_option="UNFORMATTED_VALUE")
    header = [str(c).strip() for c in valores[0]]
    idx = {c: i for i, c in enumerate(header) if c}
    planilha, repetidas = {}, 0
    for l in valores[1:]:
        n = _norm_valor(l[idx["sampleNumber"]] if len(l) > idx["sampleNumber"] else "")
        if not n:
            continue
        if n in planilha:
            repetidas += 1
        planilha.setdefault(n, l)

    faltando = sorted(n for n in api if n not in planilha)
    sem_coluna = sorted(set(als.ORDEM_COLUNAS) - set(idx))
    fora_lista = Counter()
    vazias, diferentes = defaultdict(list), defaultdict(list)
    celulas = 0
    for n, esperado in api.items():
        fora_lista.update({col.rsplit("_", 1)[0] for col, v in esperado.items()
                           if col not in als.ORDEM_COLUNAS and norm(col, v)})
        linha = planilha.get(n)
        if linha is None:
            continue
        for col in als.ORDEM_COLUNAS:
            e = norm(col, esperado.get(col))
            if not e or col not in idx:
                continue
            celulas += 1
            i = idx[col]
            p = norm(col, linha[i] if i < len(linha) else "")
            if not p:
                vazias[col].append(n)
            elif p != e:
                diferentes[col].append((n, p, e))

    total_vazias = sum(map(len, vazias.values()))
    total_dif = sum(map(len, diferentes.values()))
    escopo = f"dias {', '.join(DIAS)}" if DIAS else f"histórico {ANO_INICIAL}–hoje"
    t = [
        "## Conferência coluna a coluna — ALS results (somente leitura)",
        "",
        f"Escopo: {escopo}",
        "",
        "| | quantidade |",
        "|---|---|",
        f"| Amostras na API | {len(api)} |",
        f"| Amostras da API faltando na planilha | {len(faltando)} |",
        f"| Células conferidas (API com valor) | {celulas} |",
        f"| **Células vazias na planilha onde a API tem valor** | **{total_vazias}** |",
        f"| Células com valor diferente | {total_dif} |",
        f"| Linhas repetidas (sampleNumber) na planilha | {repetidas} |",
        f"| Colunas esperadas ausentes no cabeçalho | {len(sem_coluna)} |",
        f"| Testes da API sem coluna na planilha | {len(fora_lista)} |",
        "",
    ]
    if faltando:
        t += ["### Faltando na planilha", "", ", ".join(faltando[:500]), ""]
    if vazias:
        t += ["### Vazias por coluna (coluna: qtd — exemplos)", ""]
        t += [f"- {c}: {len(v)} — {', '.join(v[:5])}" for c, v in sorted(vazias.items(), key=lambda x: -len(x[1]))]
        t += [""]
    if diferentes:
        t += ["### Diferentes por coluna (coluna: qtd — amostra planilha→API)", ""]
        t += [f"- {c}: {len(v)} — " + "; ".join(f"{n} {p!r}→{e!r}" for n, p, e in v[:3])
              for c, v in sorted(diferentes.items(), key=lambda x: -len(x[1]))]
        t += [""]
    if sem_coluna:
        t += ["### Colunas ausentes no cabeçalho", "", ", ".join(sem_coluna), ""]
    if fora_lista:
        t += ["### Testes da API sem coluna na planilha (teste: amostras)", ""]
        t += [f"- {c}: {q}" for c, q in fora_lista.most_common()]
        t += [""]

    texto = "\n".join(t)
    print(texto)
    resumo = os.getenv("GITHUB_STEP_SUMMARY")
    if resumo:
        with open(resumo, "a", encoding="utf-8") as f:
            f.write(texto + "\n")


if __name__ == "__main__":
    inicio = time.time()
    main()
    log.info("Conferência concluída em %.0f min", (time.time() - inicio) / 60)
