"""VOX — envie um PDF, EPUB ou Word e receba o audiolivro em MP3, um arquivo por capítulo.

Vozes gratuitas: vozes neurais do Microsoft Edge (edge-tts), sem chave.
Vozes premium (opcional): defina OPENAI_API_KEY e/ou ELEVENLABS_API_KEY no ambiente.
Rodar:  pip install -r requirements.txt  &&  python app.py   ->  http://localhost:5000
"""
import asyncio, io, json, os, re, tempfile, threading, unicodedata, urllib.request, uuid, zipfile

import edge_tts
from flask import Flask, abort, jsonify, render_template, request, send_file
import leitura
from leitura import palavras, pronunciar

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


# ---------------------------------------------------------------- utilidades
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
            texto = pronunciar(cap["texto"], job["pron"])
            partes = list(blocos(texto, 3800 if MOTOR.get(job["voz"]) == "openai" else 3000))
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


def ler_pron(bruto):
    try:
        lista = json.loads(bruto or "[]")
        return [(str(d)[:80], str(p)[:120]) for d, p in lista if str(d).strip()][:300]
    except Exception:
        return []


def ler_arquivo(arq, limpar_on):
    nome = arq.filename or "livro.pdf"
    if not nome.lower().endswith(leitura.FORMATOS):
        raise ValueError("Envie um arquivo PDF, EPUB ou Word (.docx).")
    try:
        return leitura.ler(arq.read(), nome, limpar_on)
    except ValueError as e:
        if "senha" in str(e):
            raise ValueError("Este arquivo está protegido por senha. Tire a senha e envie de novo.")
        raise ValueError("Não consegui ler este arquivo. Ele pode estar corrompido.")
    except Exception:
        raise ValueError("Não consegui ler este arquivo. Ele pode estar corrompido ou protegido.")


# ---------------------------------------------------------------- rotas
@app.get("/")
def inicio():
    return render_template("index.html", vozes=VOZES, padrao=VOZ_PADRAO)


ANALISES = {}  # id -> resultado de leitura.ler, para não ler o livro duas vezes


@app.post("/analisar")
def analisar():
    """Lê o livro e devolve os capítulos encontrados (para escolher antes de narrar)."""
    arq = request.files.get("pdf")
    if not arq:
        return jsonify(erro="Envie um arquivo PDF, EPUB ou Word."), 400
    try:
        r = ler_arquivo(arq, request.form.get("limpar", "1") == "1")
    except ValueError as e:
        return jsonify(erro=str(e)), 400
    aid = uuid.uuid4().hex
    ANALISES[aid] = r
    while len(ANALISES) > 12:
        ANALISES.pop(next(iter(ANALISES)))
    return jsonify(analise=aid, titulo=r["titulo"], paginas=r["paginas"], removidos=r["removidos"],
                   tem_pdf=bool(r["pdf"]),
                   capitulos=[{"titulo": t, "minutos": max(1, round(palavras(x) / 150)), "pag_ini": a, "pag_fim": b}
                              for t, x, a, b in r["caps"]])


@app.get("/pdf/<aid>")
def pdf_convertido(aid):
    r = ANALISES.get(aid) or abort(404)
    if not r["pdf"]:
        abort(404)
    return send_file(io.BytesIO(r["pdf"]), mimetype="application/pdf")


@app.get("/texto/<aid>/<int:n>")
def texto_capitulo(aid, n):
    """Começo do texto do capítulo, já limpo e com as pronúncias, para conferir."""
    r = ANALISES.get(aid) or abort(404)
    if n >= len(r["caps"]):
        abort(404)
    t = r["caps"][n][1]
    return jsonify(texto=t[:1500], total=len(t))


@app.post("/converter")
def converter():
    r = ANALISES.get(request.form.get("analise", ""))
    if not r:
        arq = request.files.get("pdf")
        if not arq:
            return jsonify(erro="Envie o livro de novo (o site reiniciou)."), 400
        try:
            r = ler_arquivo(arq, request.form.get("limpar", "1") == "1")
        except ValueError as e:
            return jsonify(erro=str(e)), 400
    if not r["caps"]:
        return jsonify(erro="Este arquivo não tem texto selecionável (provavelmente é escaneado)."), 400
    voz = request.form.get("voz", VOZ_PADRAO)
    voz = voz if voz in MOTOR else VOZ_PADRAO
    velocidade = max(-50, min(100, int(request.form.get("velocidade", "0") or 0)))
    escolhidos = request.form.get("capitulos")  # "1,3,5" (opcional)
    job_id = uuid.uuid4().hex
    lista = [{"titulo": t, "texto": x, "status": "na fila", "progresso": 0, "pag_ini": a, "pag_fim": b,
              "minutos": max(1, round(palavras(x) / (150 * (1 + velocidade / 100))))} for t, x, a, b in r["caps"]]
    if escolhidos:
        idx = {int(i) for i in escolhidos.split(",") if i.strip().isdigit()}
        lista = [c for i, c in enumerate(lista) if i in idx] or lista
    JOBS[job_id] = {"titulo": r["titulo"], "status": "narrando", "voz": voz, "velocidade": velocidade,
                    "capitulos": lista, "paginas": r["paginas"], "pron": ler_pron(request.form.get("pron"))}
    threading.Thread(target=trabalhar, args=(job_id,), daemon=True).start()
    return jsonify(id=job_id, **publico(JOBS[job_id]))


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


TESTES = {}


@app.post("/testar")
def testar():
    """Fala um texto curto (com as pronúncias aplicadas) para conferir como ficou."""
    voz = request.form.get("voz", VOZ_PADRAO)
    voz = voz if voz in MOTOR else VOZ_PADRAO
    texto = pronunciar((request.form.get("texto") or "")[:300], ler_pron(request.form.get("pron")))
    if not texto.strip():
        return jsonify(erro="Escreva algo para testar."), 400
    chave = (voz, texto)
    if chave not in TESTES:
        mem = io.BytesIO()
        try:
            narrar_trecho(texto, voz, 0, mem)
        except Exception:
            return jsonify(erro="Não consegui gerar a voz agora. Tente de novo em instantes."), 502
        TESTES[chave] = mem.getvalue()
        while len(TESTES) > 50:
            TESTES.pop(next(iter(TESTES)))
    return send_file(io.BytesIO(TESTES[chave]), mimetype="audio/mpeg")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), threaded=True)
