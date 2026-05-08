# Spec: provedor_dados.py

## Responsabilidade
Abstrair toda comunicação com o terminal MT5 e gerenciar o cache incremental de velas no SQLite. Retorna DataFrames padronizados sem dados duplicados.

## Problema Resolvido
Sem cache, cada ciclo de 60s recarrega 500 velas por símbolo × timeframe — dados repetidos e latência desnecessária. Com cache incremental, apenas velas novas são buscadas no MT5 e persistidas.

## Schema SQLite — tabela_velas
Ver `specs/spec_repositorio.md` — gerenciado exclusivamente por `Repositorio`.

## Classe: ProvedorDados

### `__init__(self, repo: Repositorio) -> None`
- Recebe instância de `Repositorio` (injetada pelo principal.py)
- Não acessa SQLite diretamente — delega tudo ao `Repositorio`
- Não conecta MT5 no construtor (conexão lazy)

### `conectar(self) -> bool`
- Chama `mt5.initialize()`
- Retorna `True` se conectado com sucesso
- Loga erro e retorna `False` se falhar

### `desconectar(self) -> None`
- Chama `mt5.shutdown()`

### `obter_velas(self, simbolo: str, timeframe: int, quantidade: int) -> pd.DataFrame | None`

**Fluxo incremental:**
1. `SELECT MAX(tempo) FROM velas WHERE simbolo=X AND timeframe=Y`
2. **Cache vazio** → busca `quantidade` velas do MT5 via `mt5.copy_rates_from_pos`, persiste N-1 (exclui última — vela aberta)
3. **Cache existe** → busca apenas velas novas desde `ultimo_tempo + 1s` via `mt5.copy_rates_from`
   - Exclui a última vela retornada (ainda em formação)
   - `INSERT OR IGNORE` para idempotência
4. Retorna `SELECT ... ORDER BY tempo ASC LIMIT quantidade` do SQLite como DataFrame
5. Retorna `None` se símbolo inválido ou MT5 desconectado

**Regra da vela aberta:** A vela com `tempo == MAX(tempo)` do MT5 está em formação — usada na análise mas NUNCA persistida.

## Contrato do DataFrame Retornado
```
tempo       : datetime64[ns, UTC]
abertura    : float64
maxima      : float64
minima      : float64
fechamento  : float64
volume      : int64
```

## Cenários de Teste

| # | Cenário | Resultado esperado |
|---|---------|-------------------|
| 1 | MT5 indisponível | `conectar()` → `False`, log de erro |
| 2 | Símbolo inválido | `obter_velas()` → `None` |
| 3 | Cache vazio | Busca 500 velas, persiste N-1, retorna DataFrame correto |
| 4 | Cache com dados | Busca apenas delta, sem duplicatas no SQLite |
| 5 | Sem velas novas | Retorna do cache sem chamar MT5 |
| 6 | DataFrame válido | Colunas corretas, tipos corretos, ordenado por tempo ASC |
