"""Leitura de livros (PDF, EPUB, Word): extrai o texto, limpa e separa em capítulos."""
import io, re
from collections import Counter

import pymupdf

CAB = re.compile(r"^\s*(cap[íi]tulo|chapter|parte|livro|pr[óo]logo|ep[íi]logo|pref[áa]cio|introdu[çc][ãa]o)\b[\s.:—–-]*([0-9ivxlcdm]+|[a-zà-ú]+)?.{0,70}$", re.I)
NUM_PAG = re.compile(r"^\W*(p[áa]g(ina)?\.?\s*)?(\d{1,4}|[ivxlcdm]{1,7})(\s*(/|de|of)\s*\d{1,4})?\W*$", re.I)
MARCA_NOTA = re.compile(r"^\s*[\d*†‡]{1,3}\s*$")
FORMATOS = (".pdf", ".epub", ".docx")
CENA = re.compile(r"^\s*(?:(?:[*⁂#~•·o]\s*){3,7}|[*⁂#§]|[—–_-]{3,}|[—–-]\s*[xX]\s*[—–-])\s*$")  # "* * *", "⁂", "———": mudança de cena


def palavras(t):
    return len(t.split())


def limpar(t, paragrafos=False):
    """Junta as linhas num texto corrido. Marca com ¶ onde começa cada parágrafo
    (uma linha curta terminada em ponto encerra o parágrafo; uma linha com travessão começa uma fala)."""
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)          # palavras hifenizadas no fim da linha
    t = re.sub(r"\[\d{1,3}\]", "", t)                 # chamadas de nota [12]
    t = re.sub(r"https?://\S+", "link", t)
    linhas = [l.strip() for l in t.split("\n")]
    tam = sorted(len(l) for l in linhas if len(l) > 20)
    mediana = tam[len(tam) // 2] if tam else 60
    saida = []
    for l in linhas:
        if not l:
            continue
        if CENA.match(l):  # mudança de cena vira uma marca própria, com pausa maior na narração
            saida += ["¶", "§", "¶"]
            continue
        if l[0] in "—–":
            l = "¶" + l[0] + " " + l[1:].lstrip()
        saida.append(l)
        if paragrafos or (re.search(r"[.!?…:»”\"]$", l) and len(l) < 0.85 * mediana):
            saida.append("¶")
    t = " ".join(saida)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"(¶\s*)+¶", "¶", t)
    t = re.sub(r"\s*¶\s*", " ¶", t)
    return t.strip(" ¶")


def sem_marcas(t):
    return t.replace(" ¶§ ¶", " ").replace(" ¶", " ").replace("¶", "").replace("§", "")


def segmentar(texto):
    """Separa narração e falas. Devolve [(tipo, texto)], tipo 'n' (narrador) ou 'd' (diálogo).
    Entende falas com travessão (— Oi — disse ela. — Tudo bem?) e falas entre aspas (“Oi”)."""
    segs = []
    def add(tipo, t):
        t = t.strip()
        if not re.search(r"\w", t):
            if segs and t:
                segs[-1] = (segs[-1][0], segs[-1][1] + " " + t)
            return
        if segs and segs[-1][0] == tipo:
            segs[-1] = (tipo, segs[-1][1] + " " + t)
        else:
            segs.append((tipo, t))
    for par in texto.split("¶"):
        par = par.strip()
        if not par:
            continue
        if par[0] in "—–":
            partes = re.split(r"\s[—–]\s*", par[1:])
            for k, parte in enumerate(partes):
                add("d" if k % 2 == 0 else "n", parte)
            continue
        for k, parte in enumerate(re.split(r"(“[^”]{1,1200}”|\"[^\"]{1,1200}\"|«[^»]{1,1200}»)", par)):
            if k % 2 == 1:
                add("d", parte[1:-1])
            else:
                add("n", parte)
    return segs


# ---------------------------------------------------------------- limpeza de páginas (PDF/EPUB)
def _linhas_pagina(pagina, limpar_on):
    """Linhas de uma página: texto, tamanho da letra e posição vertical (0 = topo, 1 = base)."""
    d = pagina.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)
    altura = pagina.rect.height or 1
    linhas = []
    for bloco in d["blocks"]:
        if bloco.get("type") != 0:
            continue
        for linha in bloco["lines"]:
            txt, tam, chars = "", 0.0, 0
            for sp in linha["spans"]:
                t = sp["text"]
                if limpar_on and (sp["flags"] & 1) and MARCA_NOTA.match(t):
                    continue  # número sobrescrito de nota de rodapé
                txt += t
                if t.strip():
                    tam = max(tam, sp["size"]); chars += len(t)
            if txt.strip():
                linhas.append({"t": txt, "s": round(tam, 1), "y": linha["bbox"][1] / altura, "n": chars})
    return linhas


def _limpar_paginas(paginas):
    """Remove números de página, cabeçalhos/rodapés repetidos e notas de rodapé."""
    tamanhos = Counter()
    for linhas in paginas:
        for l in linhas:
            tamanhos[l["s"]] += l["n"]
    corpo = tamanhos.most_common(1)[0][0] if tamanhos else 10

    def borda(l):  # faixa onde ficam cabeçalho, rodapé e número de página
        return l["y"] < 0.09 or l["y"] > 0.88

    chave = lambda t: re.sub(r"\d+", "#", t.strip().lower())
    rep = Counter()
    for linhas in paginas:
        vistos = {chave(l["t"]) for l in linhas if borda(l)}
        rep.update(vistos)
    limite = max(3, int(0.3 * len(paginas)))
    repetidas = {k for k, v in rep.items() if v >= limite}

    saida, removidos = [], Counter()
    for linhas in paginas:
        ok = []
        for l in linhas:
            if borda(l) and NUM_PAG.match(l["t"].strip()):
                removidos["números de página"] += 1; continue
            if borda(l) and chave(l["t"]) in repetidas and not CAB.match(l["t"].strip()):
                removidos["cabeçalhos e rodapés"] += 1; continue
            ok.append(l)
        # notas de rodapé: bloco de letras menores no pé da página, abaixo do texto normal
        tem_corpo = any(abs(l["s"] - corpo) < 0.6 for l in ok)
        while tem_corpo and ok and ok[-1]["y"] > 0.55 and ok[-1]["s"] < corpo * 0.9:
            ok.pop(); removidos["linhas de notas de rodapé"] += 1
        saida.append("\n".join(l["t"] for l in ok))
    return saida, dict(removidos)


def _paginas_doc(doc, limpar_on):
    if not limpar_on:
        return [p.get_text("text") for p in doc], {}
    return _limpar_paginas([_linhas_pagina(p, True) for p in doc])


# ---------------------------------------------------------------- capítulos
def caps_sumario(toc, paginas):
    if not toc:
        return None
    nivel = min(t[0] for t in toc)
    if sum(1 for t in toc if t[0] == nivel) < 2 and any(t[0] == nivel + 1 for t in toc):
        nivel += 1
    marcas = sorted({(t[2] - 1, (t[1] or "").strip()) for t in toc if t[0] == nivel and t[2] >= 1})
    unicas = []
    for pg, titulo in marcas:
        if pg < len(paginas) and (not unicas or unicas[-1][0] != pg):
            unicas.append((pg, titulo or "Capítulo"))
    if len(unicas) < 2:
        return None
    caps = []
    if unicas[0][0] > 0:
        pre = "\n".join(paginas[:unicas[0][0]])
        if palavras(pre) > 40:
            caps.append(("Início", pre, 0, unicas[0][0] - 1))
    for k, (pg, titulo) in enumerate(unicas):
        fim = unicas[k + 1][0] if k + 1 < len(unicas) else len(paginas)
        caps.append((titulo, "\n".join(paginas[pg:fim]), pg, max(pg, fim - 1)))
    return caps


def caps_titulos(paginas, com_paginas=True):
    linhas, pag = [], []
    for n, p in enumerate(paginas):
        for l in p.split("\n"):
            linhas.append(l); pag.append(n)
    achados = {}
    for i, l in enumerate(linhas):
        t = l.strip()
        if not 3 <= len(t) <= 90 or re.search(r"\.{3,}", t):
            continue
        m = CAB.match(t)
        if m:
            achados[(m.group(1) + " " + (m.group(2) or "")).lower()] = (i, t)  # a última ocorrência vence
    marcas = sorted(achados.values())
    if len(marcas) < 2:
        return None
    P = (lambda i: pag[i]) if com_paginas else (lambda i: None)
    caps = []
    pre = "\n".join(linhas[:marcas[0][0]])
    if palavras(pre) > 40:
        caps.append(("Início", pre, P(0), P(marcas[0][0])))
    for k, (i, titulo) in enumerate(marcas):
        inicio = i + 1
        seg = linhas[i + 1].strip() if i + 1 < len(linhas) else ""
        if re.match(r"^(cap[íi]tulo|chapter|parte)\s+\S+$", titulo, re.I) and seg and len(seg) < 70 and not re.search(r"[.!?]$", seg):
            titulo += " — " + seg; inicio += 1
        fim = marcas[k + 1][0] if k + 1 < len(marcas) else len(linhas)
        caps.append((titulo, "\n".join(linhas[inicio:fim]), P(i), P(max(i, fim - 1))))
    return caps


def _por_paginas(paginas, n=10):
    return [(f"Páginas {i + 1}–{min(i + n, len(paginas))}", "\n".join(paginas[i:i + n]), i, min(i + n - 1, len(paginas) - 1))
            for i in range(0, len(paginas), n)]


def _por_tamanho(texto, alvo=3500):
    """Divide um texto sem títulos em partes de ~alvo palavras, sempre em fim de parágrafo."""
    partes, atual = [], []
    for par in texto.split("\n"):
        atual.append(par)
        if palavras("\n".join(atual)) >= alvo:
            partes.append("\n".join(atual)); atual = []
    if atual:
        partes.append("\n".join(atual))
    return [(f"Parte {i + 1}", p, None, None) for i, p in enumerate(partes)]


# ---------------------------------------------------------------- formatos
def _ler_paginado(doc, limpar_on):
    paginas, removidos = _paginas_doc(doc, limpar_on)
    titulo = ((doc.metadata or {}).get("title") or "").strip()
    _ler_paginado.autor = ((doc.metadata or {}).get("author") or "").strip()
    try:
        toc = doc.get_toc(simple=True)
    except Exception:
        toc = []
    caps = caps_sumario(toc, paginas) or caps_titulos(paginas) or _por_paginas(paginas)
    return titulo, paginas, caps, removidos


def _ler_docx(dados):
    import docx  # python-docx
    d = docx.Document(io.BytesIO(dados))
    titulo = (d.core_properties.title or "").strip()
    _ler_docx.autor = (d.core_properties.author or "").strip()
    pars = []  # (nível do título ou 0, texto)
    for p in d.paragraphs:
        t = p.text.strip()
        if not t:
            continue
        nome = (p.style.name or "").lower() if p.style is not None else ""
        m = re.match(r"(heading|título|titulo)\s*(\d)", nome)
        nivel = int(m.group(2)) if m else (1 if nome in ("title", "título") else 0)
        pars.append((nivel, t))
    caps = None
    niveis = sorted({n for n, _ in pars if n})
    for nv in niveis:
        idx = [i for i, (n, _) in enumerate(pars) if n == nv]
        if len(idx) >= 2:
            caps = []
            pre = "\n".join(t for _, t in pars[:idx[0]])
            if palavras(pre) > 40:
                caps.append(("Início", pre, None, None))
            for k, i in enumerate(idx):
                fim = idx[k + 1] if k + 1 < len(idx) else len(pars)
                caps.append((pars[i][1][:90], "\n".join(t for _, t in pars[i + 1:fim]), None, None))
            break
    texto = "\n".join(t for _, t in pars)
    if not caps:
        caps = caps_titulos([texto], com_paginas=False) or _por_tamanho(texto)
    return titulo, caps


def ler(dados, nome, limpar_on=True):
    """Devolve dict: titulo, paginas (int ou None), caps [(titulo, texto, pag_ini, pag_fim)], pdf (bytes|None), removidos."""
    ext = "." + nome.lower().rsplit(".", 1)[-1] if "." in nome else ""
    pdf, npag, removidos, autor = None, None, {}, ""
    if ext == ".docx":
        titulo, caps = _ler_docx(dados)
        autor = getattr(_ler_docx, "autor", "")
    elif ext in (".pdf", ".epub"):
        with pymupdf.open(stream=dados, filetype=ext[1:]) as doc:
            if doc.needs_pass:
                raise ValueError("protegido por senha")
            titulo, paginas, caps, removidos = _ler_paginado(doc, limpar_on)
            npag = len(paginas)
            autor = getattr(_ler_paginado, "autor", "")
            if ext == ".epub":
                pdf = doc.convert_to_pdf()  # para mostrar capa e páginas no navegador
    else:
        raise ValueError("formato não aceito")
    caps = [(t, limpar(x, ext == ".docx"), a, b) for t, x, a, b in caps]
    caps = [c for c in caps if palavras(c[1]) > 3]
    if len(titulo) <= 2 or titulo.lower().startswith(("untitled", "microsoft word", "sem título")):
        titulo = re.sub(r"\.(pdf|epub|docx)$", "", nome, flags=re.I)
    if re.fullmatch(r"(?i)(user|usuario|usuário|admin|administrator|administrador|owner|unknown|autor|python-docx|microsoft office user)\s*\d*", autor or "") or len(autor) > 80:
        autor = ""  # nomes genéricos que o Word põe sozinho
    return {"titulo": titulo, "autor": autor, "paginas": npag, "caps": caps, "pdf": pdf, "removidos": removidos}


# ---------------------------------------------------------------- pronúncias
PADRAO = [("Sr.", "senhor"), ("Sra.", "senhora"), ("Srta.", "senhorita"), ("Dr.", "doutor"), ("Dra.", "doutora"),
          ("Prof.", "professor"), ("Profa.", "professora"), ("n.º", "número"), ("nº", "número"), ("Av.", "avenida"),
          ("p. ex.", "por exemplo"), ("etc.", "etcétera")]


def pronunciar(texto, lista):
    """Troca as palavras da lista (escreve -> fala) respeitando palavras inteiras, sem diferenciar maiúsculas."""
    pares = [(d.strip(), p.strip()) for d, p in list(lista) + PADRAO if d and d.strip() and p is not None]
    vistos, unicos = set(), []
    for d, p in pares:  # a lista do usuário vem antes e tem prioridade
        if d.lower() not in vistos:
            vistos.add(d.lower()); unicos.append((d, p))
    for d, p in sorted(unicos, key=lambda x: -len(x[0])):
        texto = re.sub(r"(?<!\w)" + re.escape(d) + (r"(?!\w)" if d[-1].isalnum() else ""), lambda m: p, texto, flags=re.I)
    return texto
