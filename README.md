# Monitor de Gift Cards - Shopping do Inter

Monitor diário de ofertas de cashback em gift cards do [Shopping do Inter](https://shopping.inter.co/gift-card), com envio de alertas via Telegram e execução agendada pelo GitHub Actions.

O frontend do Inter carrega os gift cards dinamicamente via API JSON pública (`GET https://marketplace-api.web.bancointer.com.br/site/giftcard/inter/v1/giftcards/search?lang=pt-BR&category=`), sem necessidade de autenticação ou scraping via navegador.

## Marcas monitoradas (`config.json`)

Por padrão, o monitor acompanha:
- Uber
- Pague Menos
- Petz (`Gift Card Cupom Petz`)
- Google Play (`Gift Card Google Play`, cobrindo "Google Pay")
- App Store (`Gift Card App Store`, cobrindo "Apple Store")
- Outback (`Gift Card Outback Steakhouse` e `Gift Card Outback Golden Card`)
- Assaí
- Carrefour (`Gift Card Carrefour`, excluindo `Carrefour Drogaria`)
- Atacadão
- Shopee (`Gift Card Cupom Shopee`)

> O alerta é disparado quando `cashbackValue > min_cashback_percent` (padrão: **3%**, ou seja, acima de 3%). Você também pode definir `"min_cashback"` individualmente em qualquer marca dentro de `config.json` (por exemplo, se quiser alertar Outback apenas acima de 10%).

## Testar localmente

Sem dependências externas (usa apenas biblioteca padrão do Python 3.12+):

```bash
python3 monitor.py --dry-run
```

Para rodar os testes unitários:

```bash
python3 -m unittest discover -s tests -v
```

Para enviar um alerta real localmente:

```bash
export TELEGRAM_BOT_TOKEN="seu_token"
export TELEGRAM_CHAT_ID="seu_chat_id"
python3 monitor.py
```

## Configurar o Bot no Telegram

1. Fale com `@BotFather` no Telegram, envie `/newbot` e copie o **token** gerado.
2. Envie uma mensagem qualquer para o seu novo bot no Telegram.
3. Descubra seu `chat_id` abrindo `https://api.telegram.org/bot<SEU_TOKEN>/getUpdates` (procure por `"chat":{"id":...}`).

## Agendar no GitHub Actions

1. Crie um repositório no GitHub e envie os arquivos deste projeto.
2. No repositório, acesse **Settings → Secrets and variables → Actions** e cadastre os **Repository secrets**:
   - `TELEGRAM_BOT_TOKEN`: token do seu bot
   - `TELEGRAM_CHAT_ID`: seu chat ID (ou ID de um grupo/canal)
3. *(Opcional)* Em **Variables**, crie `MIN_CASHBACK` se quiser ajustar o limiar global (ex.: `5`) sem editar `config.json`.
4. O workflow em `.github/workflows/monitor.yml` roda todos os dias às **12:00 UTC (09:00 BRT)** e também possui o botão **Run workflow** na aba **Actions** para testes manuais (inclusive com opção `dry_run`).
