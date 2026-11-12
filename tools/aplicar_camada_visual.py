"""Aplica templates/_camada_visual.css e templates/_menu_lateral.html aos templates e reembute os dados nos
dashboards já publicados.

Os dashboards são HTML autocontido (abrem pelo htmlpreview, sem arquivos ao lado), então a camada visual
é copiada para dentro do <style> de cada template, entre dois marcadores, e o menu lateral (com a faixa do
rodapé) entra logo depois de <body>, também entre marcadores; a página inicial recebe só o menu. Rodar de
novo troca os blocos.
Os dashboards da lista PARES são regravados com os MESMOS dados que já tinham: nada é coletado nem
recalculado. Uso:  python tools/aplicar_camada_visual.py
"""
import json
import os
import re

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAMADA = os.path.join("templates", "_camada_visual.css")
BLOCO = re.compile(r"/\* ===== Camada visual v2 — início.*?/\* ===== Camada visual v2 — fim ===== \*/\n", re.S)
MENU = os.path.join("templates", "_menu_lateral.html")
BLOCO_MENU = re.compile(r"<!-- ===== Menu lateral v1 — início.*?<!-- ===== Menu lateral v1 — fim ===== -->\n", re.S)
MARCA = "/*__DADOS__*/null"
SO_MENU = ["index.html"]  # páginas com estilo próprio: recebem o menu, não a camada

# template -> dashboards gerados a partir dele que devem ser regravados
PARES = {
    "templates/dashboard_template.html": ["resultados/dashboard.html"],
    "templates/dashboard_fiis_template.html": ["resultados/dashboard_fiis.html"],
    "templates/dashboard_eleicao_template.html": ["resultados/dashboard_eleicao.html"],
    "templates/dashboard_pos_eleicao_template.html": ["resultados/dashboard_pos_eleicao.html"],
    "templates/painel_besst_template.html": ["resultados/painel_besst_2026_11_06.html", "resultados/painel_besst_2026_11_12.html"],
    "templates/painel_ao_vivo_template.html": [],
    "leituras/2026_11_11/dashboard_eleicao_template.html": ["leituras/2026_11_11/dashboard_eleicao.html"],
    "leituras/2026_11_12/dashboard_eleicao_template.html": ["leituras/2026_11_12/dashboard_eleicao.html"],
}


def ler(caminho):
    with open(os.path.join(RAIZ, caminho), encoding="utf-8") as f:
        return f.read()


def gravar(caminho, texto):
    with open(os.path.join(RAIZ, caminho), "w", encoding="utf-8", newline="\n") as f:
        f.write(texto)


def dados_de(html):
    """Devolve o JSON embutido na linha `const DADOS = …;` de um dashboard gerado."""
    m = re.search(r"^(?:const|let) DADOS = (.*);\s*$", html, re.M)
    if not m:
        raise ValueError("linha de DADOS não encontrada")
    json.loads(m.group(1))  # garante que é o payload inteiro
    return m.group(1)


def com_menu(html, menu):
    html = BLOCO_MENU.sub("", html)
    assert html.count("<body>\n") == 1
    return html.replace("<body>\n", "<body>\n" + menu)


def main():
    camada, menu = ler(CAMADA), ler(MENU)
    for pagina in SO_MENU:
        gravar(pagina, com_menu(ler(pagina), menu))
        print("menu", pagina)
    for template, saidas in PARES.items():
        html = BLOCO.sub("", BLOCO_MENU.sub("", ler(template)))
        assert html.count("</style>") == 1 and MARCA in html, template
        html = com_menu(html.replace("</style>", camada + "</style>"), menu)
        gravar(template, html)
        print("template", template)
        for saida in saidas:
            gravar(saida, html.replace(MARCA, dados_de(ler(saida))))
            print("  regravado", saida)


if __name__ == "__main__":
    main()
