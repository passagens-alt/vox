"""Escreve por extenso números, datas, dinheiro, frações, horas e algarismos romanos,
para a narração ler do jeito certo em português (e as vozes multilíngues não lerem em inglês)."""
import re

from num2words import num2words

MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
         "setembro", "outubro", "novembro", "dezembro"]
FRACOES = {2: "meio", 3: "terço", 4: "quarto", 5: "quinto", 6: "sexto", 7: "sétimo", 8: "oitavo",
           9: "nono", 10: "décimo", 100: "centésimo", 1000: "milésimo"}
ROMANOS = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
FEMININOS = {"um": "uma", "dois": "duas", "duzentos": "duzentas", "trezentos": "trezentas",
             "quatrocentos": "quatrocentas", "quinhentos": "quinhentas", "seiscentos": "seiscentas",
             "setecentos": "setecentas", "oitocentos": "oitocentas", "novecentos": "novecentas"}
NOMES_FEM = {"elizabeth", "isabel", "maria", "catarina", "ana", "vitória", "leonor", "carlota", "teresa", "joana"}


def extenso(n, fem=False):
    t = num2words(int(n), lang="pt_BR").replace(",", "")
    if fem:
        # "mil" e "milhões" não mudam; só as partes antes deles mudam de gênero no fim do número
        t = " ".join(FEMININOS.get(p, p) for p in t.split())
    return t


def ordinal(n, fem=False):
    t = num2words(int(n), lang="pt_BR", to="ordinal").replace(",", "")
    return " ".join(p[:-1] + "a" if fem and p.endswith("o") else p for p in t.split())


def inteiro(txt):
    return int(txt.replace(".", ""))


def decimal(txt, fem=False):
    if "," in txt:
        a, b = txt.split(",", 1)
        dec = " ".join(extenso(c) for c in b) if b.startswith("0") else extenso(int(b))
        return f"{extenso(inteiro(a), fem)} vírgula {dec}"
    return extenso(inteiro(txt), fem)


def romano(r):
    r = r.upper(); total = 0
    for i, c in enumerate(r):
        v = ROMANOS[c]
        total += -v if i + 1 < len(r) and ROMANOS[r[i + 1]] > v else v
    return total


def eh_romano(r):
    return bool(re.fullmatch(r"M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})", r)) and r != ""


def dinheiro(valor, centavos, moeda="real"):
    n = inteiro(valor)
    nomes = {"real": ("real", "reais"), "dolar": ("dólar", "dólares"), "euro": ("euro", "euros")}[moeda]
    partes = []
    if n or not centavos:
        de = " de" if n >= 1_000_000 and n % 1_000_000 == 0 else ""
        partes.append(f"{extenso(n)}{de} {nomes[0] if n == 1 else nomes[1]}")
    if centavos and int(centavos):
        c = int(centavos)
        partes.append(f"{extenso(c)} {'centavo' if c == 1 else 'centavos'}")
    return " e ".join(partes)


def fracao(a, b):
    a, b = int(a), int(b)
    if b == 2:
        return "meio" if a == 1 else f"{extenso(a)} meios"
    nome = FRACOES.get(b)
    if nome:
        return f"{extenso(a)} {nome}{'s' if a > 1 else ''}"
    return f"{extenso(a)} {extenso(b)} avos"


NUM = r"\d{1,3}(?:\.\d{3})+|\d+"
REGRAS = []


def regra(padrao, flags=0):
    def dec(f):
        REGRAS.append((re.compile(padrao, flags), f))
        return f
    return dec


@regra(rf"(R\$|US\$|€)\s?({NUM})(?:,(\d{{2}}))?(?:\s?(mil|milhões|milhão|bilhões|bilhão)\b)?")
def _dinheiro(m):
    moeda = {"R$": "real", "US$": "dolar", "€": "euro"}[m.group(1)]
    if m.group(4):  # "R$ 2 milhões", "R$ 1,5 bilhão"
        num = decimal(m.group(2) + ("," + m.group(3) if m.group(3) else ""))
        plural = {"real": "reais", "dolar": "dólares", "euro": "euros"}[moeda]
        return f"{num} {m.group(4)} de {plural}" if m.group(4) != "mil" else f"{num} mil {plural}"
    return dinheiro(m.group(2), m.group(3), moeda)


@regra(r"\b(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b")
def _data(m):
    d, mes, a = int(m.group(1)), int(m.group(2)), m.group(3)
    if not (1 <= d <= 31 and 1 <= mes <= 12):
        return m.group(0)
    ano = int(a) if len(a) == 4 else (2000 + int(a) if int(a) < 50 else 1900 + int(a))
    dia = "primeiro" if d == 1 else extenso(d)
    return f"{dia} de {MESES[mes - 1]} de {extenso(ano)}"


@regra(r"\b(\d{1,2})\s?de\s(janeiro|fevereiro|março|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro)\b", re.I)
def _dia_mes(m):
    d = int(m.group(1))
    return f"{'primeiro' if d == 1 else extenso(d)} de {m.group(2)}"


def _horas(h, mi):
    horas = f"{extenso(h, fem=True)} {'hora' if h == 1 else 'horas'}"
    return horas if mi == 0 else f"{horas} e {extenso(mi)} {'minuto' if mi == 1 else 'minutos'}"


@regra(r"\b([01]?\d|2[0-3])h([0-5]\d)(?:min)?\b")
def _hora(m):
    return _horas(int(m.group(1)), int(m.group(2)))


@regra(r"\b(às|as|das|pelas|até|desde|entre|e)\s([01]?\d|2[0-3]):([0-5]\d)\b(?!:)", re.I)
def _hora_dois_pontos(m):  # só com "às 9:05", para não confundir com versículos (João 3:16)
    return f"{m.group(1)} {_horas(int(m.group(2)), int(m.group(3)))}"


@regra(r"\b([01]?\d|2[0-3])\s?h\b")
def _hora_cheia(m):
    h = int(m.group(1))
    return f"{extenso(h, fem=True)} {'hora' if h == 1 else 'horas'}"


@regra(r"(?:\b(de|entre)\s)?\b(1\d{3}|20\d{2})\s?[-–]\s?(1\d{3}|20\d{2})\b", re.I)
def _anos(m):
    a, b = extenso(m.group(2)), extenso(m.group(3))
    pre = m.group(1) or "de"
    return f"{pre} {a} e {b}" if pre.lower() == "entre" else f"{pre} {a} a {b}"


@regra(r"\b(\d{1,4})/(\d{1,4})\b(\s+(?:de\s+)?(\w+))?")
def _fracao(m):
    a, b = int(m.group(1)), int(m.group(2))
    if not (0 < b and a < 1000):
        return m.group(0)
    resto = m.group(3) or ""
    if a == 1 and b == 2 and resto and feminina(m.group(4)):
        return "meia" + resto
    return fracao(a, b) + resto


@regra(rf"({NUM}(?:,\d+)?)\s?%")
def _porcento(m):
    return f"{decimal(m.group(1))} por cento"


@regra(r"\b(\d{1,4})\s?([ºª°])(?=\W|$)")
def _ordinal(m):
    return ordinal(m.group(1), fem=m.group(2) == "ª")


@regra(r"\b(s[ée]c(?:ulo|\.)|século)\s+([IVXLCDM]{1,6})\b", re.I)
def _seculo(m):
    r = m.group(2)
    return f"século {extenso(romano(r))}" if eh_romano(r.upper()) else m.group(0)


@regra(r"\b(cap[íi]tulo|parte|livro|volume|vol\.|tomo|ato|cena|canto|art\.|artigo)\s+([IVXLCDM]{1,7})\b(?![a-zà-ú])", re.I)
def _romano_secao(m):
    r = m.group(2).upper()
    return f"{m.group(1)} {extenso(romano(r))}" if eh_romano(r) else m.group(0)


@regra(r"\b([A-ZÁÉÍÓÚÂÊÔ][a-zà-ú]{2,})\s+([IVX]{1,4})\b(?![a-zà-ú'’])")
def _rei(m):  # Pedro II, Luís XIV, Elizabeth II
    r = m.group(2)
    if not eh_romano(r):
        return m.group(0)
    n, fem = romano(r), m.group(1).lower() in NOMES_FEM or m.group(1).lower().endswith("a")
    return f"{m.group(1)} {ordinal(n, fem) if n <= 10 else extenso(n, fem)}"


UNIDADES = {"km/h": ("quilômetro por hora", "quilômetros por hora"), "km": ("quilômetro", "quilômetros"),
            "kg": ("quilo", "quilos"), "cm": ("centímetro", "centímetros"), "mm": ("milímetro", "milímetros"),
            "ml": ("mililitro", "mililitros"), "m²": ("metro quadrado", "metros quadrados"),
            "°C": ("grau", "graus")}


@regra(rf"({NUM}(?:,\d+)?)\s?(km/h|km|kg|cm|mm|ml|m²|°C)(?![\w/])")
def _unidade(m):
    sing, plur = UNIDADES[m.group(2)]
    v = m.group(1)
    return f"{decimal(v)} {sing if v in ('1', '1,0') else plur}"


@regra(r"\b\d{1,3}(?:\.\d{3})+(?:,\d+)?\b|\b\d+,\d+\b")
def _separadores(m):  # 1.000.000 e 3,5
    return decimal(m.group(0))


MASC_EM_A = {"dia", "dias", "mapa", "mapas", "problema", "problemas", "sistema", "sistemas", "programa", "programas",
             "tema", "temas", "planeta", "planetas", "idioma", "idiomas", "clima", "poema", "poemas", "cinema",
             "telefonema", "drama", "dramas", "esquema", "teorema", "pijama", "sofá", "guarda", "guardas", "atleta", "atletas"}


def feminina(palavra):
    p = (palavra or "").lower()
    return p not in MASC_EM_A and (p.endswith("a") or p.endswith("as") or p.endswith("ade") or p.endswith("ades")
                                   or p in {"vez", "vezes", "mulher", "mulheres", "noite", "noites", "tarde", "tardes", "gente", "mãe", "mães", "flor", "flores", "cor", "cores", "árvore", "árvores", "lição", "lições", "canção", "canções", "mão", "mãos"})


@regra(r"(?<![\w.,/:%$])(\d{1,9})(?![\w%°ºª/:]|[.,]\d)(\s+(\w+))?")
def _inteiro(m):  # números soltos: "2 horas" -> "duas horas"; 1990 -> "mil novecentos e noventa"
    n = int(m.group(1))
    if len(m.group(1)) > 1 and m.group(1).startswith("0"):  # 007, 0800: um dígito por vez
        return " ".join(extenso(c) for c in m.group(1)) + (m.group(2) or "")
    fem = bool(m.group(3)) and feminina(m.group(3))
    return extenso(n, fem) + (m.group(2) or "")


def falar_numeros(texto):
    for padrao, f in REGRAS:
        texto = padrao.sub(lambda m: f(m), texto)
    return texto
