"""Clube do livro: um link para amigos ouvirem o mesmo livro e comentarem trechos.

Os arquivos ficam em VOX_DADOS (no plano grátis do Render, uma pasta temporária que some quando o
servidor reinicia). Por isso o aparelho de quem criou o clube guarda a chave e reenvia tudo sozinho
quando o clube some ("recriar"), e cada aparelho reenvia os próprios comentários.
"""
import hashlib, json, os, re, shutil, threading, time, uuid

from flask import Blueprint, abort, jsonify, render_template, request, send_file

bp = Blueprint("clube", __name__)
DADOS = os.environ.get("VOX_DADOS") or os.path.join(os.environ.get("TMPDIR", "/tmp"), "vox", "clubes")
os.makedirs(DADOS, exist_ok=True)
TRAVA = threading.Lock()
HEX = re.compile(r"^[0-9a-f]{32}$")
ID_COM = re.compile(r"^[\w-]{1,48}$")
MAX_COMENTARIOS = 3000


def pasta(cid):
    if not HEX.match(cid or ""):
        abort(404)
    return os.path.join(DADOS, cid)


def ler_json(caminho, padrao):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return padrao


def gravar_json(caminho, dados):
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False)
    os.replace(tmp, caminho)


def meta_de(cid):
    m = ler_json(os.path.join(pasta(cid), "meta.json"), None)
    if not m:
        abort(404)
    return m


def checar_dono(cid, m=None):
    m = m or meta_de(cid)
    if (request.form.get("chave") or (request.get_json(silent=True) or {}).get("chave")) != m["chave"]:
        abort(403)
    return m


def cid_da_chave(chave):
    """O link vem da chave secreta: só quem tem a chave consegue recriar o clube depois que o servidor reinicia."""
    return hashlib.sha256(chave.encode()).hexdigest()[:32]


def texto(v, n):
    return str(v or "").strip()[:n]


@bp.post("/clube")
def criar():
    """Cria um clube. Com cid+chave de um clube que sumiu do servidor, recria com o mesmo link."""
    cid, chave = request.form.get("cid") or "", request.form.get("chave") or ""
    try:
        caps = json.loads(request.form.get("capitulos") or "[]")[:500]
    except ValueError:
        caps = []
    caps = [{"titulo": texto(c.get("titulo"), 200) or f"Capítulo {i + 1}", "minutos": int(c.get("minutos") or 1)}
            for i, c in enumerate(caps) if isinstance(c, dict)]
    if not caps:
        return jsonify(erro="O livro não tem capítulos."), 400
    with TRAVA:
        if cid:
            if not (HEX.match(cid) and HEX.match(chave)) or cid_da_chave(chave) != cid:
                return jsonify(erro="Esse clube pertence a outra pessoa."), 403
            atual = ler_json(os.path.join(DADOS, cid, "meta.json"), None)
            if atual and atual["chave"] != chave:
                return jsonify(erro="Esse clube pertence a outra pessoa."), 403
        else:
            chave = uuid.uuid4().hex
            cid = cid_da_chave(chave)
        os.makedirs(os.path.join(DADOS, cid), exist_ok=True)
        anterior = ler_json(os.path.join(DADOS, cid, "meta.json"), {}) or {}
        gravar_json(os.path.join(DADOS, cid, "meta.json"), {
            "cid": cid, "chave": chave, "titulo": texto(request.form.get("titulo"), 200) or "Livro",
            "autor": texto(request.form.get("autor"), 120), "dono": texto(request.form.get("dono"), 40) or "Alguém",
            "capitulos": caps, "criado": anterior.get("criado") or int(time.time() * 1000), "apagados": anterior.get("apagados", [])})
    return jsonify(cid=cid, chave=chave)


@bp.post("/clube/<cid>/cap/<int:n>")
def enviar_capitulo(cid, n):
    m = checar_dono(cid)
    if not 0 <= n < len(m["capitulos"]):
        abort(404)
    audio = request.files.get("audio")
    if not audio:
        return jsonify(erro="Faltou o áudio do capítulo."), 400
    p = pasta(cid)
    audio.save(os.path.join(p, f"cap_{n}.mp3.tmp")); os.replace(os.path.join(p, f"cap_{n}.mp3.tmp"), os.path.join(p, f"cap_{n}.mp3"))
    try:
        leg = json.loads(request.form.get("legenda") or "null")
    except ValueError:
        leg = None
    if isinstance(leg, list):
        gravar_json(os.path.join(p, f"leg_{n}.json"), leg[:20000])
    return jsonify(ok=True)


@bp.post("/clube/<cid>/capa")
def enviar_capa(cid):
    checar_dono(cid)
    f = request.files.get("capa")
    if f:
        f.save(os.path.join(pasta(cid), "capa.jpg"))
    return jsonify(ok=True)


@bp.post("/clube/<cid>/encerrar")
def encerrar(cid):
    checar_dono(cid)
    shutil.rmtree(pasta(cid), ignore_errors=True)
    return jsonify(ok=True)


@bp.get("/clube/<cid>")
def pagina(cid):
    pasta(cid)  # só valida o formato; o clube pode estar "dormindo" e a página explica
    return render_template("clube.html", cid=cid)


@bp.get("/clube/<cid>/info")
def info(cid):
    m, p = meta_de(cid), pasta(cid)
    return jsonify(titulo=m["titulo"], autor=m["autor"], dono=m["dono"], tem_capa=os.path.exists(os.path.join(p, "capa.jpg")),
                   capitulos=[{**c, "pronto": os.path.exists(os.path.join(p, f"cap_{i}.mp3"))} for i, c in enumerate(m["capitulos"])])


@bp.get("/clube/<cid>/audio/<int:n>")
def audio(cid, n):
    f = os.path.join(pasta(cid), f"cap_{n}.mp3")
    if not os.path.exists(f):
        abort(404)
    return send_file(f, mimetype="audio/mpeg", conditional=True)


@bp.get("/clube/<cid>/legenda/<int:n>")
def legenda(cid, n):
    return jsonify(frases=ler_json(os.path.join(pasta(cid), f"leg_{n}.json"), []))


@bp.get("/clube/<cid>/capa")
def capa(cid):
    f = os.path.join(pasta(cid), "capa.jpg")
    if not os.path.exists(f):
        abort(404)
    return send_file(f, mimetype="image/jpeg")


def limpar_comentario(c):
    if not isinstance(c, dict) or not ID_COM.match(str(c.get("id") or "")):
        return None
    t = texto(c.get("texto"), 1000)
    if not t:
        return None
    try:
        cap, seg = int(c.get("cap") or 0), round(float(c.get("t") or 0), 1)
    except (TypeError, ValueError):
        return None
    pai = str(c.get("pai") or "")
    return {"id": c["id"], "autor": texto(c.get("autor"), 40) or "Alguém", "texto": t, "cap": cap, "t": max(0, seg),
            "quando": int(c.get("quando") or time.time() * 1000), **({"pai": pai} if ID_COM.match(pai) else {}),
            **({"dono": 1} if c.get("dono") else {})}


@bp.get("/clube/<cid>/comentarios")
def comentarios(cid):
    m = meta_de(cid)
    return jsonify(comentarios=ler_json(os.path.join(pasta(cid), "comentarios.json"), []), apagados=m.get("apagados", []))


@bp.post("/clube/<cid>/comentarios")
def comentar(cid):
    """Junta comentários novos pelo id (reenviar o mesmo comentário não duplica)."""
    m = meta_de(cid)
    corpo = request.get_json(silent=True) or {}
    novos = [x for x in (limpar_comentario(c) for c in (corpo.get("comentarios") or [])[:200]) if x]
    dono = corpo.get("chave") == m["chave"]
    with TRAVA:
        arq = os.path.join(pasta(cid), "comentarios.json")
        lista = ler_json(arq, [])
        ids, apagados = {c["id"] for c in lista}, set(m.get("apagados", []))
        for c in novos:
            if c["id"] in ids or c["id"] in apagados or len(lista) >= MAX_COMENTARIOS:
                continue
            if c.get("dono") and not dono:
                c.pop("dono")
            lista.append(c); ids.add(c["id"])
        lista.sort(key=lambda c: (c["cap"], c["t"], c["quando"]))
        gravar_json(arq, lista)
    return jsonify(comentarios=lista, apagados=m.get("apagados", []))


@bp.post("/clube/<cid>/comentarios/apagar")
def apagar_comentario(cid):
    """Só quem criou o clube apaga comentários (o id fica guardado para não voltar)."""
    m = checar_dono(cid)
    cid_c = str((request.get_json(silent=True) or {}).get("id") or "")
    with TRAVA:
        arq = os.path.join(pasta(cid), "comentarios.json")
        lista = [c for c in ler_json(arq, []) if c["id"] != cid_c and c.get("pai") != cid_c]
        gravar_json(arq, lista)
        m["apagados"] = (m.get("apagados", []) + [cid_c])[-5000:]
        gravar_json(os.path.join(pasta(cid), "meta.json"), m)
    return jsonify(comentarios=lista, apagados=m["apagados"])
