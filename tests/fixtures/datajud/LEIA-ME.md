# Fixtures do DataJud

Respostas **reais** da API pública do DataJud (CNJ), coletadas com
`ferramentas/probe_datajud.py`, servem de base para escrever e testar o leitor da
Fase 2 (ver `docs/VISAO.md`), já que o ambiente de desenvolvimento não alcança a API.

## Como gerar

```
export DATAJUD_API_KEY="<chave pública da wiki do CNJ>"
python ferramentas/probe_datajud.py --tribunal tjsp --tamanho 5
```

O script salva `datajud_<tribunal>_<data>.json`. Coloque o arquivo aqui (ou anexe na
conversa). O resumo impresso diz se veio `valorCausa` e confirma que **não** vêm as partes.

## Privacidade

O DataJud público não expõe nome/CPF/CNPJ das partes. Ainda assim, confira o arquivo
antes de commitar e remova qualquer dado sensível que porventura apareça.
