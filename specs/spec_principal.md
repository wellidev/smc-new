# Spec: principal.py

## Responsabilidade
Orquestrar o loop de varredura contínua. Coordena todos os módulos, gerencia o banco de dados e a deduplicação de sinais.

## Fluxo por Ciclo

```
para cada símbolo em ATIVOS_MONITORADOS:
    1. obter velas H4 e M15 via ProvedorDados
    2. detectar_captura_liquidez(velas_h4, PERIODO_SWING, LIMIAR_PAVIO)
    3. detectar_quebra_estrutura(velas_h4, símbolo, PERIODO_SWING)
    4. mapear_zonas_interesse(velas_h4, símbolo)  → (obs, fvgs)
    5. preco_atual = velas_m15.iloc[-1].fechamento
    6. para cada captura × bos × ob × fvg:
       verificar_confluencia(captura, bos, obs, fvgs, preco_atual)
    7. se confluência detectada:
       a. id_sinal = SHA1(simbolo + ob.id + fvg.id)
       b. verificar no SQLite se id_sinal já existe
       c. se novo → INSERT em tabela_sinais + enviar alerta Telegram
```

## Schema SQLite
Ver `specs/spec_repositorio.md` — toda comunicação com o banco é feita via `Repositorio`.

## Repositorio
`principal.py` instancia `Repositorio(CAMINHO_BANCO)` e o injeta em `ProvedorDados`. Operações de deduplicação de sinais usam `repo.sinal_ja_disparado()` e `repo.persistir_sinal()`.

## Tratamento de Erros no Loop

| Situação | Comportamento |
|----------|---------------|
| Falha de conexão MT5 | Log crítico, tentativa de reconexão, aguarda `INTERVALO * 2` |
| Símbolo indisponível | Log warning, pula para próximo símbolo |
| Falha no Telegram | Log error, sinal é persistido no SQLite mesmo assim |
| Exceção não tratada no ciclo | Log exception com traceback, loop continua |

## Inicialização
1. Carregar variáveis de ambiente via `dotenv`
2. Configurar logging com formato: `%(asctime)s | %(levelname)s | %(name)s | %(message)s`
3. Instanciar `Repositorio(CAMINHO_BANCO)`
4. Instanciar `ProvedorDados(repo)`, `Notificador`
5. Conectar MT5 (com retry de 3 tentativas espaçadas de 5s)
6. Entrar no loop principal com `time.sleep(INTERVALO_VARREDURA_SEGUNDOS)` entre ciclos
7. Ao encerrar (KeyboardInterrupt): desconectar MT5 e fechar SQLite

## Logging
- Nível: `INFO` por padrão (configurável via env `SMC_LOG_LEVEL`)
- Um logger por módulo: `logging.getLogger(__name__)`
- Logs de cada ciclo: símbolo processado, capturas encontradas, BOS detectados, sinais disparados
