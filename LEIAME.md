# VOX — seu PDF virando audiolivro

Envie um PDF, escolha a voz e receba o audiolivro em MP3, **um arquivo por capítulo** (ou todos juntos num ZIP). Dá para ouvir direto no site, com tocador que passa sozinho para o próximo capítulo.

## Como os capítulos são encontrados

1. Pelo sumário interno do PDF (os marcadores que aparecem na lateral do leitor de PDF).
2. Se não houver, pelos títulos no texto: "Capítulo 1", "Capítulo II", "Prólogo", "Epílogo", "Parte 1"...
3. Se nada for encontrado, o livro é dividido a cada 10 páginas.

Antes de narrar, você vê a lista e pode desmarcar capítulos.

## Vozes

Gratuitas, sem chave (vozes neurais do Microsoft Edge):
- **Thalita** (Brasil, a mais natural), **Francisca**, **Antônio**
- **Andrew** e **Ava** (multilíngues, falam português muito bem)
- **Raquel** e **Duarte** (Portugal)

Premium (opcional, pagas por uso). Basta definir a chave antes de iniciar o site e as vozes aparecem sozinhas:
- OpenAI: `OPENAI_API_KEY=sua-chave` → vozes Nova, Onyx e Sage
- ElevenLabs: `ELEVENLABS_API_KEY=sua-chave` (e, se quiser, `ELEVENLABS_VOICE_ID`) → a voz mais expressiva

## Rodar no seu computador

1. Instale o Python 3.10 ou mais novo (python.org).
2. No terminal, dentro desta pasta:

```
pip install -r requirements.txt
python app.py
```

3. Abra http://localhost:5000

## Colocar na internet

Funciona no Render, Railway ou PythonAnywhere. No Render, crie um "Web Service" a partir desta pasta (num repositório do GitHub):
- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app --workers 1 --threads 8 --timeout 600`

Use **1 worker**: os pedidos ficam guardados na memória do processo.

## Observações

- PDFs escaneados (só imagem) não têm texto e precisam de OCR antes.
- Um livro de 200 páginas leva alguns minutos; cada capítulo fica disponível assim que termina.
- As vozes gratuitas vêm do serviço de leitura em voz alta do Edge (biblioteca `edge-tts`). Não é um serviço oficial com garantia; para um site comercial, prefira as vozes premium.
- Só distribua audiolivros de obras que você tem direito de usar.
