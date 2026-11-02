# Dataset Sintetico de Ordenes (500 000 Registros)

Este directorio alberga el dataset oficial generado deterministicamente para el benchmark de contencion CPU/RAM.

## Especificaciones
- **Cantidad de registros:** 500,000 pedidos JSON anidados (~3 niveles de profundidad).
- **Semilla pseudoaleatoria:** `DEFAULT_SEED = 42`
- **Total de items:** ~1,500,000 (promedio exacto de 3.0 items/orden).
- **Peso en disco:** ~613 MB (NDJSON / `.jsonl`).
- **Checksum SHA-256 oficial:**
  `747396b2705e0ff4f7b842cff00a82eb67dc3682a082e6f171b6475e43f2884d  orders.jsonl`

## Regeneracion Determinista
Debido a que GitHub prohibe subir archivos individuales que superen los 100 MB, el archivo plano `orders.jsonl` esta excluido por `.gitignore`. Para reproducir o generar el archivo identico en local:

```bash
python3 synthetic_data_generator.py --count 500000 --seed 42 --output data/orders.jsonl
shasum -a 256 data/orders.jsonl
```

