# SMC Alerter

Bot de análise técnica baseado em Smart Money Concepts (SMC) que monitora ativos no MetaTrader 5 e envia alertas via Telegram quando há confluência de sinais.

## Pré-requisitos

- Python 3.11+
- [MetaTrader 5](https://www.metatrader5.com/) instalado e **aberto** com uma conta ativa
- Bot do Telegram criado via [@BotFather](https://t.me/BotFather)
- Windows (a biblioteca `MetaTrader5` só funciona no Windows)

## Instalação

```bash
# 1. Clone o repositório
git clone <url-do-repo>
cd smc-new

# 2. Crie e ative um ambiente virtual
python -m venv .venv
.venv\Scripts\activate

# 3. Instale o pacote em modo editável (necessário pelo src layout)
pip install -e .

# 4. Instale as dependências de desenvolvimento (testes)
pip install -r requirements.txt
```

## Configuração

Crie um arquivo `.env` na raiz do projeto:

```env
# Obrigatório — credenciais do bot Telegram
SMC_TELEGRAM_TOKEN=seu_token_aqui
SMC_TELEGRAM_CHAT_ID=seu_chat_id_aqui

# Opcional — credenciais MT5 (se não preenchidas, usa a sessão aberta no MT5)
SMC_MT5_LOGIN=
SMC_MT5_PASSWORD=
SMC_MT5_SERVER=
SMC_MT5_PATH=

# Opcional — nível de log (padrão: INFO)
SMC_LOG_LEVEL=INFO
```

> Como obter o `TELEGRAM_CHAT_ID`: envie uma mensagem para o bot e acesse `https://api.telegram.org/bot<TOKEN>/getUpdates`.

## Execução

Com o MetaTrader 5 aberto e o `.env` preenchido:

```bash
python principal.py
```

O bot escaneia todos os ativos configurados em `src/smc/configuracoes.py` a cada 60 segundos e envia um alerta no Telegram quando as 4 condições SMC forem confirmadas simultaneamente (Captura de Liquidez + BOS + Order Block + FVG).

## Testes

```bash
# Todos os testes
pytest -v

# Módulo específico
pytest testes/teste_analisador_smc.py -v

# Teste específico
pytest testes/teste_analisador_smc.py::TestVerificarConfluencia::test_confluencia_completa_baixa -v
```

Os testes não dependem do MT5 real nem de conexão com Telegram.

## Verificação de tipos

```bash
mypy src/smc/
```

## Ativos monitorados

Configurados em `src/smc/configuracoes.py`:

| Categoria | Ativos |
|-----------|--------|
| Forex | EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD |
| Índices | US30, US500, US100, GER40, UK100 |
| Metais | XAUUSD, XAGUSD, BRENT |
| Cripto | BTCUSD, ETHUSD |

## Estrutura do projeto

```
smc-new/
├── principal.py          # Entry point
├── src/smc/              # Código de produção
│   ├── analisador_smc.py # Lógica SMC (BOS, OB, FVG, confluência)
│   ├── provedor_dados.py # Integração MT5 + cache SQLite
│   ├── repositorio.py    # Persistência de sinais (deduplicação)
│   ├── notificador.py    # Envio de alertas Telegram
│   ├── configuracoes.py  # Parâmetros e variáveis de ambiente
│   └── principal.py      # Loop principal
├── testes/               # Testes unitários
├── specs/                # Especificações dos módulos
└── banco_dados/          # SQLite criado automaticamente na primeira execução
```
