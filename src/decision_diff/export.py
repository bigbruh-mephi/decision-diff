"""Self-contained downloadable HTML report: verdict, decision swap, change clusters and every check row.

No scripts and no external resources (strict CSP); every number links back into the application, so the analyst
can drill from the downloaded file into the interactive report.
"""

import html
import urllib.parse

import orjson

CSS = """
:root{--bg:#fff;--fg:#0f0f0f;--muted:#6b6560;--line:#eee9e3;--fail:#ad1242;--fail-bg:#fdebf0;--low:#8c5a68;
--low-bg:#faf3f5;--pass:#1f7a4d;--accent:#d93a14}
@media (prefers-color-scheme:dark){:root{--bg:#151210;--fg:#f5f1ed;--muted:#a89f97;--line:#2f2925;--fail:#ff7a9c;
--fail-bg:#3b1722;--low:#c59aa6;--low-bg:#2a1f22;--pass:#6fcf9f;--accent:#ff8562}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 Inter,system-ui,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;
margin:32px 0 10px}p.sub{color:var(--muted);margin:0 0 20px}.tiles{display:grid;grid-template-columns:repeat(auto-fit,
minmax(170px,1fr));gap:10px}.tile{border:1px solid var(--line);border-radius:20px;padding:12px 14px}.tile b{display:
block;font-size:24px;font-variant-numeric:tabular-nums}.tile span{color:var(--muted);font-size:12px}table{width:100%;
border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);
vertical-align:top}th{color:var(--muted);font-weight:600;font-size:12px}td.n{text-align:right;font-variant-numeric:
tabular-nums;white-space:nowrap}code{font:12px ui-monospace,JetBrains Mono,monospace}a{color:var(--accent)}
tr.fail td{background:var(--fail-bg)}tr.fail .res{color:var(--fail);font-weight:700}tr.low td{background:var(--low-bg);
color:var(--low)}tr.low .res{color:var(--low);font-weight:600}tr.pass .res{color:var(--pass);font-weight:600}
.badge{display:inline-block;padding:1px 8px;border-radius:999px;border:1px solid var(--line);font-size:11px}
.wrap{overflow-x:auto}footer{margin-top:40px;color:var(--muted);font-size:12px}
"""

ORIGIN = {"logic": "логика стратегии", "input": "входные данные", "mixed": "смешанный"}


def _e(x):
    return html.escape("" if x is None else str(x))


def _pct(n, d):
    return f"{n / d * 100:.1f}%" if d else "—"


def render(rep, run, check_results, base):
    """HTML text of the report for ``rep`` (a RunReport) and the run row; links point to ``base``."""
    rid = str(run["id"])
    app_url = f"{base}/runs/{rid}"
    s = rep.summary({})
    clusters = rep.cluster_rows({})

    def link(text, view, scope=None):
        q = "?scope=" + urllib.parse.quote(orjson.dumps(scope).decode()) if scope else ""
        return f'<a href="{_e(app_url + view + q)}">{_e(text)}</a>'

    tiles = [("Заявок", s["apps"], ""), ("С различиями", s["changed"], _pct(s["changed"], s["apps"])),
             ("Сменилось решение", s["decision_changed"], _pct(s["decision_changed"], s["apps"])),
             ("Различия есть, решение то же", s["changed_same_decision"], _pct(s["changed_same_decision"], s["apps"]))]
    if s["approval_before"] is not None:
        tiles.append(("Доля одобрений", f"{s['approval_before'] * 100:.1f}% → {s['approval_after'] * 100:.1f}%", ""))
    out = [(f"<!doctype html><html lang=ru><head><meta charset=utf-8><meta name=viewport "
           f"content='width=device-width,initial-scale=1'><title>Отчёт {_e(run['name'])}</title><style>{CSS}</style>"
           f"</head><body><main><h1>Прогон {_e(run['name'])}</h1><p class=sub>Анализ изменений: BEFORE (MASTER) и AFTER (BETA)"
           f" · завершён {_e(run.get('finished_at'))} · {link('открыть интерактивный отчёт', '')}</p><div class=tiles>")]
    out += [f"<div class=tile><b>{_e(v)}</b><span>{_e(k)} {_e(p)}</span></div>" for k, v, p in tiles]
    out.append("</div><h2>Куда сместились решения</h2><div class=wrap><table><tr><th>BEFORE<th>AFTER<th>Заявок")
    out += [f"<tr><td>{_e(r['before'])}<td>{_e(r['after'])}<td class=n>{r['apps']}" for r in s["swap"]]
    out.append("</table></div><h2>Кластеры изменений</h2><div class=wrap><table><tr><th>#<th>Источник<th>Заявок"
               "<th>Сменилось решение<th>Главные переходы")
    for c in clusters:
        if c.get("cid") is None:
            out.append(f"<tr><td>…<td>ещё {c['clusters']}<td class=n>{c['apps']}<td class=n>{c['decision_changed']}<td>")
            continue
        tr = "<br>".join(f"<code>{_e(t['path'])}</code> {_e(t['before'])} → {_e(t['after'])} ({t['apps']})"
                         for t in c["transitions"])
        out.append(f"<tr><td>{link(c['cid'], f'/clusters/{c['cid']}')}<td><span class=badge>{_e(ORIGIN.get(c['origin'], c['origin']))}</span>"
                   f"<td class=n>{c['apps']}<td class=n>{c['decision_changed']}<td>{tr}")
    out.append("</table></div><h2>Автопроверки</h2><div class=wrap><table><tr><th>Проверка<th>Срез<th>Аргумент<th>Числа"
               "<th>Метрика<th>Результат<th>Заявки")
    rows = [(r["label"], x) for r in check_results for x in r["rows"]]
    rows.sort(key=lambda t: (t[1]["RESULT"] != "FAIL", t[1]["CONFIDENCE"] != "HIGH", t[0]))
    for label, x in rows:
        cls = "pass" if x["RESULT"] == "PASS" else ("low" if x["CONFIDENCE"] == "LOW" else "fail")
        res = x["RESULT"] + (" · низкая уверенность" if x["CONFIDENCE"] == "LOW" else "")
        apps = link(f"{len(x['APP_IDS'])} шт.", "/apps", {"apps": x["APP_IDS"]}) if x["APP_IDS"] else "—"
        out.append(f"<tr class={cls}><td>{_e(label)}<td>{_e(x['SOURCE'])}<td><code>{_e(x['ARGUMENT'])}</code>"
                   f"<td class=n>{_e(x['CNT'])} / {_e(x['TOTAL'])}<td class=n>{_e(x['METRIC'])}<td class=res>{_e(res)}"
                   f"<td>{apps}")
    for r in check_results:
        if r.get("error"):
            out.append(f"<tr class=fail><td>{_e(r['label'])}<td colspan=6>ошибка: <code>{_e(r['error'])}</code>")
    out.append(f"</table></div><footer>Decision Diff · прогон {_e(rid)} · файл самодостаточный, ссылки ведут на "
               f"{_e(base)}</footer></main></body></html>")
    return "".join(out)
