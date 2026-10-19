"""VOX — envie um PDF, EPUB ou Word e receba o audiolivro em MP3, um arquivo por capítulo.

Vozes gratuitas: vozes neurais do Microsoft Edge (edge-tts), sem chave.
Vozes premium (opcional): defina OPENAI_API_KEY e/ou ELEVENLABS_API_KEY no ambiente.
Rodar:  pip install -r requirements.txt  &&  python app.py   ->  http://localhost:5000
"""
import asyncio, io, json, os, re, tempfile, threading, unicodedata, urllib.request, uuid, zipfile

import edge_tts
from flask import Flask, abort, jsonify, render_template, request, send_file
import leitura
from leitura import palavras, pronunciar, segmentar, sem_marcas

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
    """Narra e devolve o tempo de cada palavra [(segundos, palavra)], para o texto acompanhar o áudio."""
    com = edge_tts.Communicate(texto, voz, rate=f"{velocidade:+d}%", boundary="WordBoundary")
    marcas = []
    async for p in com.stream():
        if p["type"] == "audio":
            arquivo.write(p["data"])
        elif p["type"] in ("WordBoundary", "SentenceBoundary"):
            marcas.append((p["offset"] / 1e7, p["text"]))
    return marcas


def _post(url, headers, corpo):
    req = urllib.request.Request(url, data=json.dumps(corpo).encode(), headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return r.read()


def narrar_trecho(texto, voz, velocidade, arquivo):
    """Escreve o MP3 do trecho em `arquivo`. Devolve os tempos das palavras (só nas vozes do Edge) ou None."""
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
        inicio = arquivo.tell()
        try:
            return asyncio.run(_edge(texto, voz, velocidade, arquivo))
        except Exception:
            if voz == VOZ_PADRAO:
                raise
            arquivo.seek(inicio); arquivo.truncate()
            return asyncio.run(_edge(texto, VOZ_PADRAO, velocidade, arquivo))  # voz indisponível: usa a Francisca


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
EDGE_BYTES_POR_SEG = 48000 / 8  # as vozes do Edge vêm em MP3 de 48 kbps, taxa fixa


MAIUSC = "A-ZÁÉÍÓÚÂÊÔÃÕÇ"
ABREV = re.compile(r"(?:^|\s)(?:sr|sra|srta|dr|dra|prof|profa|av|st|sto|sta|n[ºo]|p|pp|cap|vol|ed|etc|ex|obs|[A-Z])\.$", re.I)


def frases(t):
    """Divide em frases sem quebrar em abreviações (Dr., Sra., J. R.) nem em '— disse ela'."""
    pedacos = re.split(rf"(?<=[.!?…])\s+(?=[\"“«(]|[—–]\s*[{MAIUSC}]|[{MAIUSC}0-9])", t.strip())
    out = []
    for p in pedacos:
        if out and ABREV.search(out[-1]):
            out[-1] += " " + p
        elif p.strip():
            out.append(p)
    return out


def unidades(texto, voz, voz_dialogo, pron):
    """Divide o capítulo em frases: o que aparece na tela (s), o que é falado (f), a voz (v) e se abre parágrafo (p)."""
    out = []
    for par in texto.split("¶"):
        par = par.strip()
        if not par:
            continue
        primeira = True
        if voz_dialogo and voz_dialogo != voz:
            travessao = par[0] in "—–"
            segs = []
            for tipo, seg in segmentar("¶" + par):
                mostra = ("— " + seg) if travessao else (f"“{seg}”" if tipo == "d" else seg)
                segs.append((voz_dialogo if tipo == "d" else voz, seg, mostra))
        else:
            segs = [(voz, par, par)]
        for v, seg, mostra in segs:
            falas, telas = frases(seg), frases(mostra)
            if len(falas) != len(telas):
                falas, telas = [seg], [mostra]
            for f, t in zip(falas, telas):
                fala = sem_marcas(pronunciar(f, pron)).strip()
                if fala:
                    out.append({"v": v, "s": t, "f": fala, "p": primeira})
                    primeira = False
    return out


def blocos_de(unids, limite_por_voz):
    """Junta frases seguidas da mesma voz em blocos de até ~3000 letras."""
    bl = []
    for u in unids:
        lim = limite_por_voz(u["v"])
        if bl and bl[-1]["v"] == u["v"] and len(bl[-1]["texto"]) + len(u["f"]) + 1 <= lim:
            u["pos"] = len(bl[-1]["texto"]) + 1
            bl[-1]["texto"] += " " + u["f"]; bl[-1]["us"].append(u)
        else:
            u["pos"] = 0
            bl.append({"v": u["v"], "texto": u["f"], "us": [u]})
    return bl


def alinhar(bloco, marcas, inicio):
    """Dá a cada frase do bloco o tempo (em segundos, no arquivo do capítulo) da sua primeira palavra."""
    pos, cursor, txt = [], 0, bloco["texto"]
    for t, palavra in marcas:
        i = txt.find(palavra, cursor)
        if i >= 0:
            pos.append((i, t)); cursor = i + len(palavra)
    k = 0
    for u in bloco["us"]:
        while k < len(pos) and pos[k][0] < u["pos"]:
            k += 1
        u["t"] = round(inicio + pos[k][1], 2) if k < len(pos) else None


def trabalhar(job_id):
    job = JOBS[job_id]
    try:
        for n, cap in enumerate(job["capitulos"]):
            cap["status"] = "narrando"
            unids = unidades(cap["texto"], job["voz"], job.get("voz_dialogo"), job["pron"])
            partes = blocos_de(unids, lambda v: 3800 if MOTOR.get(v) == "openai" else 3000)
            caminho = os.path.join(PASTA, f"{job_id}-{n:03d}.mp3")
            tempo_ok = True  # o tempo de início de cada bloco só é exato enquanto tudo vem do Edge
            with open(caminho, "wb") as mp3:
                for i, bl in enumerate(partes, 1):
                    inicio = mp3.tell() / EDGE_BYTES_POR_SEG
                    marcas = narrar_trecho(bl["texto"], bl["v"], job["velocidade"], mp3)
                    try:  # a sincronia do texto nunca pode atrapalhar a narração
                        if tempo_ok and isinstance(marcas, list) and marcas:
                            alinhar(bl, marcas, inicio)
                        else:
                            tempo_ok = False
                    except Exception:
                        tempo_ok = False
                    cap["progresso"] = round(100 * i / len(partes))
            cap["legenda"] = [{"s": u["s"], "t": u.get("t"), **({"p": 1} if u["p"] else {})} for u in unids]
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
@app.get("/ping")
def ping():
    return "ok"


@app.errorhandler(413)
def grande_demais(_):
    return jsonify(erro="O arquivo passa do limite de 80 MB. Tente uma versão menor."), 413


@app.errorhandler(500)
def erro_interno(_):
    return jsonify(erro="Algo deu errado ao ler este arquivo. Tente de novo; se repetir, tente outro arquivo."), 500


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
    t = r["caps"][n][1].replace(" ¶", "\n")
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
                    "capitulos": lista, "paginas": r["paginas"], "pron": ler_pron(request.form.get("pron")),
                    "voz_dialogo": request.form.get("voz_dialogo") if request.form.get("voz_dialogo") in MOTOR else None}
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


@app.get("/legenda/<job_id>/<int:n>")
def legenda(job_id, n):
    """Frases do capítulo com o segundo em que cada uma começa (para o texto acompanhar o áudio)."""
    job = JOBS.get(job_id) or abort(404)
    if n >= len(job["capitulos"]) or job["capitulos"][n]["status"] != "pronto":
        abort(404)
    return jsonify(frases=job["capitulos"][n].get("legenda", []))


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
    texto = sem_marcas(pronunciar((request.form.get("texto") or "")[:300], ler_pron(request.form.get("pron"))))
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
