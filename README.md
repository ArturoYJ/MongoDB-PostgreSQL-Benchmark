# Benchmark de contención de recursos: MongoDB vs. PostgreSQL JSONB

Experimento personal para explorar el comportamiento de MongoDB y PostgreSQL ante consultas sobre pedidos sintéticos anidados, bajo límites definidos de CPU y memoria.

## Descripción general

- **Dataset:** 500,000 documentos sintéticos de pedidos en formato JSON Lines (`data/orders.jsonl`, excluido de Git).
- **Motores registrados en la evidencia disponible:** MongoDB 9.0.4 con WiredTiger y PostgreSQL 18.6 con JSONB.
- **Cargas de trabajo:** Q1 (expansión de arreglos y agrupación) y Q2 (filtrado, ordenamiento y Top-1000).
- **Evidencia:** el CSV original conserva 198 intentos históricos; los datos derivados seleccionan 162 claves de intento. De estos, 93 se consideran candidatos para análisis de rendimiento y 69 se excluyen por fallos o falta de evidencia concluyente. «Candidato» no significa que todos los estados estén demostrados para cada intento.
- Los límites de CPU y memoria cambian juntos entre perfiles. Las asociaciones observadas no demuestran que CFS, por sí solo, haya causado cambios en latencia o rendimiento.

Los datos disponibles incluyen P50/P95/P99, QPS, memoria, revisión de estados y planes de ejecución. Las figuras son descriptivas: un punto faltante no representa rendimiento igual a cero. Las etiquetas históricas `oom` o `spill` sin evidencia vinculada al intento individual no se presentan como resultados confirmados.

## Evidencia y archivos de resultados

| Artefacto | Propósito |
|---|---|
| [`results/metrics.csv`](results/metrics.csv) | CSV original preservado, con 14 columnas y 198 filas; SHA-256 `f3139481670f04f4387030ed929fcbf39f15278b6bd1e440c9b11321b9dca1f3`. |
| [`results/final/metrics_selected_derived.csv`](results/final/metrics_selected_derived.csv) | Dataset derivado de 162 filas, con el contrato requerido de 14 columnas. |
| [`results/final/status_adjudication_162.csv`](results/final/status_adjudication_162.csv) | Cruce de auditoría con los estados originales y derivados, la evidencia y el motivo de cada decisión. |
| [`results/final/condition_summary_54.csv`](results/final/condition_summary_54.csv) | Resumen de las 54 condiciones de benchmark; interpretar con cautela las condiciones incompletas. |
| [`results/plans/`](results/plans/) y [`results/logs/`](results/logs/) | Planes archivados y registros de diagnóstico. Algunos planes pueden corresponder a preflight y no a un intento individual. |
| [`results/final/figures/`](results/final/figures/) | Figuras descriptivas derivadas de los resultados; la figura 4 relaciona tiempo CFS y QPS. |

No edites `results/metrics.csv` para normalizar estados históricos. Toda interpretación de estados debe quedar en artefactos derivados y conservar trazabilidad hasta la fila original.

## Estructura del repositorio

```text
.
├── benchmark_runner/              # Módulos Python de benchmark y carga de datos
├── data/
│   ├── orders.jsonl               # Se genera localmente; Git lo ignora
│   └── orders.manifest.json       # Metadatos de procedencia del dataset y generador
├── queries/                       # Definiciones de consultas para MongoDB y PostgreSQL
├── results/
│   ├── final/                     # Tablas derivadas, figuras y artefactos finales
│   ├── logs/                      # Registros de diagnóstico archivados
│   ├── metrics.csv                # CSV original de mediciones, preservado
│   └── plans/                     # Planes de ejecución archivados
├── tests/                         # Pruebas unitarias
├── dataset_manifest.py            # Crea o valida el manifiesto de procedencia
├── generate_derived_dataset.py    # Genera datasets de resultados y resúmenes
├── generate_figures.py            # Genera figuras a partir de los resultados
├── qa_verification.py             # Verifica artefactos; no reproduce el benchmark
├── run_benchmarks.sh              # Modos de preflight, piloto y matriz completa
└── synthetic_data_generator.py    # Generador de pedidos sintéticos
```

## Preparación y ejecución segura

### 1. Preparar el entorno y el dataset

Las herramientas del repositorio requieren Python 3.9 o posterior. Docker con soporte para cgroups v2 solo es necesario para las operaciones de preflight o benchmark. Instala las dependencias fijadas:

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.lock.txt
```

El dataset local está excluido de Git. Si no está disponible, puedes generarlo con los valores predeterminados del generador:

```bash
python3 synthetic_data_generator.py --count 500000 --output data/orders.jsonl --seed 42
```

Crea el manifiesto rastreable después de generar los datos o valida un dataset y manifiesto existentes:

```bash
python3 dataset_manifest.py --write
python3 dataset_manifest.py --check
```

El manifiesto registra el tamaño del archivo, el número de registros, los SHA-256 del dataset y del generador, la versión actual de Python y los valores predeterminados de reproducción. Como no se archivó el comando ni el entorno originales de generación, el manifiesto no afirma que la semilla 42 se haya usado para el dataset histórico.

### 2. Revisar los modos del ejecutor antes de usarlos

No existe un modo de simulación ni una opción `--plan`. Si se ejecuta sin opciones, el script usa `--pilot` por defecto. No ejecutes los siguientes comandos salvo que pretendas iniciar esas operaciones:

| Comando | Efecto |
|---|---|
| `./run_benchmarks.sh --preflight` | Inicia o comprueba la pila de telemetría y ejecuta la ruta de preparación de dataset e índices; no mide cargas, pero puede afectar servicios o bases de datos locales. |
| `./run_benchmarks.sh --pilot` | Ejecuta celdas piloto medidas para ambos motores. |
| `./run_benchmarks.sh --full` | Ejecuta la matriz de benchmark de 162 mediciones. |

No se necesita ningún modo de benchmark para ejecutar las pruebas unitarias, validar el manifiesto o procesar los resultados archivados. En esta actualización no se volvió a ejecutar el piloto ni la matriz completa.

### 3. Procesar los resultados archivados

Los siguientes comandos procesan evidencia existente; no ejecutan mediciones del benchmark:

```bash
python3 evaluate_telemetry.py
python3 generate_derived_dataset.py
python3 generate_figures.py
python3 qa_verification.py
```

Los scripts de análisis pueden regenerar artefactos derivados. Conserva el CSV original y compara sus hashes antes y después de cualquier flujo de análisis.

## Validación

Ejecuta las pruebas unitarias, la comprobación de sintaxis del shell y la compilación de Python sin iniciar modos de benchmark:

```bash
python3 -m unittest discover -s tests -v
bash -n run_benchmarks.sh
python3 -m compileall -q benchmark_runner dataset_manifest.py evaluate_telemetry.py generate_derived_dataset.py generate_figures.py qa_verification.py synthetic_data_generator.py
python3 dataset_manifest.py --check
```

`qa_verification.py` valida artefactos preservados y derivados disponibles. Superar las pruebas estáticas o de artefactos **no** demuestra la reproducibilidad empírica de la matriz histórica.

## Entregables del repositorio

- **Métricas originales:** [`results/metrics.csv`](results/metrics.csv).
- **Métricas derivadas y adjudicación:** [`results/final/metrics_selected_derived.csv`](results/final/metrics_selected_derived.csv) y [`results/final/status_adjudication_162.csv`](results/final/status_adjudication_162.csv).
- **Código, configuración, consultas y evidencia archivada:** archivos del repositorio descritos anteriormente.

El dataset original no se incluye en Git; puede generarse localmente y verificarse con el manifiesto. El repositorio de un compañero se tomó únicamente como referencia de estructura documental, no como fuente de mediciones ni conclusiones de este proyecto.
