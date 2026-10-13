"""VOX — envie um PDF e receba o audiolivro em MP3, um arquivo por capítulo.

Vozes gratuitas: vozes neurais do Microsoft Edge (edge-tts), sem chave.
Vozes premium (opcional): defina OPENAI_API_KEY e/ou ELEVENLABS_API_KEY no ambiente.
Rodar:  pip install -r requirements.txt  &&  python app.py   ->  http://localhost:5000
"""
import asyncio, io, json, os, re, tempfile, threading, unicodedata, urllib.request, uuid, zipfile

import edge_tts
from flask import Flask, abort, jsonify, render_template, request, send_file
from pypdf import PdfReader

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024
PASTA = os.path.join(tempfile.gettempdir(), "vox")
os.makedirs(PASTA, exist_ok=True)
JOBS = {}

# ---------------------------------------------------------------- vozes
VOZES = [
    # id, nome, descrição, motor
    ("pt-BR-ThalitaMultilingualNeural", "Thalita", "feminina · Brasil · a mais natural", "edge"),
    ("pt-BR-FranciscaNeural", "Francisca", "feminina · Brasil", "edge"),
    ("pt-BR-AntonioNeural", "Antônio", "masculina · Brasil", "edge"),
    ("en-US-AndrewMultilingualNeural", "Andrew", "masculina · multilíngue, fala português", "edge"),
    ("en-US-AvaMultilingualNeural", "Ava", "feminina · multilíngue, fala português", "edge"),
    ("pt-PT-RaquelNeural", "Raquel", "feminina · Portugal", "edge"),
    ("pt-PT-DuarteNeural", "Duarte", "masculino · Portugal", "edge"),
]
if os.environ.get("OPENAI_API_KEY"):
    VOZES += [
        ("openai:nova", "Nova", "feminina · premium (OpenAI)", "openai"),
        ("openai:onyx", "Onyx", "masculina grave · premium (OpenAI)", "openai"),
        ("openai:sage", "Sage", "feminina calma · premium (OpenAI)", "openai"),
    ]
if os.environ.get("ELEVENLABS_API_KEY"):
    VOZES += [("eleven:" + os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"),
               "ElevenLabs", "premium · a mais expressiva", "eleven")]
VOZ_PADRAO = "pt-BR-FranciscaNeural"
MOTOR = {v[0]: v[3] for v in VOZES}


async def _edge(texto, voz, velocidade, arquivo):
    com = edge_tts.Communicate(texto, voz, rate=f"{velocidade:+d}%")
    async for p in com.stream():
        if p["type"] == "audio":
            arquivo.write(p["data"])


def _post(url, headers, corpo):
    req = urllib.request.Request(url, data=json.dumps(corpo).encode(), headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def narrar_trecho(texto, voz, velocidade, arquivo):
    motor = MOTOR.get(voz, "edge")
    if motor == "openai":
        arquivo.write(_post("https://api.openai.com/v1/audio/speech",
                            {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"]},
                            {"model": "gpt-4o-mini-tts", "voice": voz.split(":", 1)[1], "input": texto,
                             "instructions": "Narre em português do Brasil, como um audiolivro, com calma e boa entonação.",
                             "speed": 1 + velocidade / 100, "response_format": "mp3"}))
    elif motor == "eleven":
        arquivo.write(_post(f"https://api.elevenlabs.io/v1/text-to-speech/{voz.split(':', 1)[1]}",
                            {"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Accept": "audio/mpeg"},
                            {"text": texto, "model_id": "eleven_multilingual_v2"}))
    else:
        try:
            asyncio.run(_edge(texto, voz, velocidade, arquivo))
        except Exception:
            if voz == VOZ_PADRAO:
                raise
            asyncio.run(_edge(texto, VOZ_PADRAO, velocidade, arquivo))  # voz indisponível: usa a Francisca


# ---------------------------------------------------------------- texto e capítulos
CAB = re.compile(r"^\s*(cap[íi]tulo|chapter|parte|livro|pr[óo]logo|ep[íi]logo|pref[áa]cio|introdu[çc][ãa]o)\b[\s.:—–-]*([0-9ivxlcdm]+|[a-zà-ú]+)?.{0,70}$", re.I)


def limpar(t):
    t = re.sub(r"(\w)-\n(\w)", r"\1\2", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def palavras(t):
    return len(t.split())


def caps_sumario(leitor, paginas):
    try:
        itens = [i for i in leitor.outline if not isinstance(i, list)]
        marcas = sorted({(leitor.get_destination_page_number(i), (i.title or "").strip()) for i in itens})
    except Exception:
        return None
    unicas = []
    for pg, titulo in marcas:
        if not unicas or unicas[-1][0] != pg:
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


def caps_titulos(paginas):
    linhas, pag = [], []
    for n, p in enumerate(paginas):
        for l in p.split("\n"):
            linhas.append(l)
            pag.append(n)
    achados = {}
    for i, l in enumerate(linhas):
        t = l.strip()
        if not 3 <= len(t) <= 90 or re.search(r"\.{3,}", t):
            continue
        m = CAB.match(t)
        if m:
            chave = (m.group(1) + " " + (m.group(2) or "")).lower()
            achados[chave] = (i, t)  # a última ocorrência vence (as primeiras costumam ser o sumário)
    marcas = sorted(achados.values())
    if len(marcas) < 2:
        return None
    caps = []
    pre = "\n".join(linhas[:marcas[0][0]])
    if palavras(pre) > 40:
        caps.append(("Início", pre, 0, pag[marcas[0][0]]))
    for k, (i, titulo) in enumerate(marcas):
        inicio = i + 1
        seg = linhas[i + 1].strip() if i + 1 < len(linhas) else ""
        if re.match(r"^(cap[íi]tulo|chapter|parte)\s+\S+$", titulo, re.I) and seg and len(seg) < 70 and not re.search(r"[.!?]$", seg):
            titulo += " — " + seg
            inicio += 1
        fim = marcas[k + 1][0] if k + 1 < len(marcas) else len(linhas)
        caps.append((titulo, "\n".join(linhas[inicio:fim]), pag[i], pag[max(i, fim - 1)]))
    return caps


def capitulos(dados):
    leitor = PdfReader(io.BytesIO(dados))
    paginas = [(p.extract_text() or "") for p in leitor.pages]
    titulo = ""
    try:
        titulo = (leitor.metadata.title or "").strip() if leitor.metadata else ""
    except Exception:
        pass
    caps = caps_sumario(leitor, paginas) or caps_titulos(paginas)
    if not caps:
        caps = [(f"Páginas {i + 1}–{min(i + 10, len(paginas))}", "\n".join(paginas[i:i + 10]), i, min(i + 9, len(paginas) - 1))
                for i in range(0, len(paginas), 10)]
    caps = [(t, limpar(x), a, b) for t, x, a, b in caps]
    return titulo, len(paginas), [c for c in caps if palavras(c[1]) > 3]


def blocos(texto, tamanho=3000):
    frases = re.split(r"(?<=[.!?…])\s+", texto)
    atual = ""
    for f in frases:
        while len(f) > tamanho:
            yield f[:tamanho]
            f = f[tamanho:]
        if len(atual) + len(f) > tamanho and atual:
            yield atual
            atual = ""
        atual += f + " "
    if atual.strip():
        yield atual


def nome_arquivo(t):
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", t).strip("-")[:60] or "capitulo"


# ---------------------------------------------------------------- trabalho em segundo plano
def trabalhar(job_id):
    job = JOBS[job_id]
    try:
        for n, cap in enumerate(job["capitulos"]):
            cap["status"] = "narrando"
            partes = list(blocos(cap["texto"], 3800 if MOTOR.get(job["voz"]) == "openai" else 3000))
            caminho = os.path.join(PASTA, f"{job_id}-{n:03d}.mp3")
            with open(caminho, "wb") as mp3:
                for i, parte in enumerate(partes, 1):
                    narrar_trecho(parte, job["voz"], job["velocidade"], mp3)
                    cap["progresso"] = round(100 * i / len(partes))
            cap.update(status="pronto", caminho=caminho)
        job["status"] = "pronto"
    except Exception as e:  # noqa: BLE001
        job.update(status="erro", erro=str(e))


def publico(job):
    return {
        "titulo": job["titulo"], "status": job["status"], "erro": job.get("erro"),
        "capitulos": [{"titulo": c["titulo"], "status": c["status"], "progresso": c["progresso"],
                       "minutos": c["minutos"], "pag_ini": c["pag_ini"], "pag_fim": c["pag_fim"]} for c in job["capitulos"]],
    }


# ---------------------------------------------------------------- rotas
@app.get("/")
def inicio():
    return render_template("index.html", vozes=VOZES, padrao=VOZ_PADRAO)


@app.post("/converter")
def converter():
    arq = request.files.get("pdf")
    if not arq or not arq.filename.lower().endswith(".pdf"):
        return jsonify(erro="Envie um arquivo .pdf."), 400
    try:
        titulo, npag, caps = capitulos(arq.read())
    except Exception:
        return jsonify(erro="Não consegui ler este PDF. Ele pode estar protegido por senha."), 400
    if not caps:
        return jsonify(erro="Este PDF não tem texto selecionável (provavelmente é escaneado). Passe por OCR antes."), 400
    voz = request.form.get("voz", VOZ_PADRAO)
    voz = voz if voz in MOTOR else VOZ_PADRAO
    velocidade = max(-50, min(100, int(request.form.get("velocidade", "0") or 0)))
    escolhidos = request.form.get("capitulos")  # "1,3,5" (opcional)
    job_id = uuid.uuid4().hex
    titulo = titulo if len(titulo) > 2 and not titulo.lower().startswith(("untitled", "microsoft word")) \
        else re.sub(r"\.pdf$", "", arq.filename, flags=re.I)
    lista = [{"titulo": t, "texto": x, "status": "na fila", "progresso": 0, "pag_ini": a, "pag_fim": b,
              "minutos": max(1, round(palavras(x) / (150 * (1 + velocidade / 100))))} for t, x, a, b in caps]
    if escolhidos:
        idx = {int(i) for i in escolhidos.split(",") if i.strip().isdigit()}
        lista = [c for i, c in enumerate(lista) if i in idx] or lista
    JOBS[job_id] = {"titulo": titulo, "status": "narrando", "voz": voz, "velocidade": velocidade,
                    "capitulos": lista, "paginas": npag}
    threading.Thread(target=trabalhar, args=(job_id,), daemon=True).start()
    return jsonify(id=job_id, **publico(JOBS[job_id]))


@app.post("/analisar")
def analisar():
    """Só lê o PDF e devolve os capítulos encontrados (para escolher antes de narrar)."""
    arq = request.files.get("pdf")
    if not arq:
        return jsonify(erro="Envie um arquivo .pdf."), 400
    try:
        titulo, npag, caps = capitulos(arq.read())
    except Exception:
        return jsonify(erro="Não consegui ler este PDF."), 400
    if len(titulo) <= 2 or titulo.lower().startswith(("untitled", "microsoft word")):
        titulo = re.sub(r"\.pdf$", "", arq.filename, flags=re.I)
    return jsonify(titulo=titulo, paginas=npag,
                   capitulos=[{"titulo": t, "minutos": max(1, round(palavras(x) / 150)), "pag_ini": a, "pag_fim": b}
                              for t, x, a, b in caps])


@app.get("/status/<job_id>")
def status(job_id):
    job = JOBS.get(job_id) or abort(404)
    return jsonify(publico(job))


@app.get("/audio/<job_id>/<int:n>")
def audio(job_id, n):
    job = JOBS.get(job_id) or abort(404)
    if n >= len(job["capitulos"]) or job["capitulos"][n]["status"] != "pronto":
        abort(404)
    cap = job["capitulos"][n]
    nome = f"{n + 1:02d}-{nome_arquivo(cap['titulo'])}.mp3"
    return send_file(cap["caminho"], mimetype="audio/mpeg", as_attachment=bool(request.args.get("baixar")),
                     download_name=nome, conditional=True)


@app.get("/zip/<job_id>")
def baixar_zip(job_id):
    job = JOBS.get(job_id) or abort(404)
    prontos = [(n, c) for n, c in enumerate(job["capitulos"]) if c["status"] == "pronto"]
    if not prontos:
        abort(404)
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_STORED) as z:
        for n, c in prontos:
            z.write(c["caminho"], f"{n + 1:02d}-{nome_arquivo(c['titulo'])}.mp3")
    mem.seek(0)
    return send_file(mem, mimetype="application/zip", as_attachment=True,
                     download_name=nome_arquivo(job["titulo"]) + "-audiolivro.zip")


AMOSTRAS = {}


@app.get("/amostra/<voz>")
def amostra(voz):
    if voz not in MOTOR:
        abort(404)
    if voz not in AMOSTRAS:
        mem = io.BytesIO()
        narrar_trecho("Olá! Esta é a minha voz. Vamos ouvir o seu livro juntos?", voz, 0, mem)
        AMOSTRAS[voz] = mem.getvalue()
    return send_file(io.BytesIO(AMOSTRAS[voz]), mimetype="audio/mpeg")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), threaded=True)
