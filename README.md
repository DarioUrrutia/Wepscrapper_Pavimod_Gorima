# ANAS Lavori in Corso — Web Scraper

Scraper iterativo para descargar y enriquecer datos de **obras viales en curso** desde el sitio oficial de ANAS (Agenzia Nazionale per la Sicurezza delle Strade), cruzados con información del registro público **OpenCUP**.

---

## Regiones cubiertas

| Región | Código ANAS |
|---|---|
| Sicilia | SICILIA |
| Basilicata | BASILICATA |
| Puglia | PUGLIA |
| Calabria | CALABRIA |
| Molise | MOLISE |
| Campania | CAMPANIA |

---

## Columnas del CSV generado

| Columna | Fuente | Descripción |
|---|---|---|
| `Regione` | ANAS | Región italiana |
| `Codice_Strada` | ANAS | Código de carretera (ej: SS106) |
| `Nome_Strada` | ANAS | Nombre completo de la vía |
| `Cup` | ANAS | Código CUP del proyecto |
| `Descrizione` | ANAS | Descripción completa de los trabajos |
| `Tipo_Lavoro` | ANAS | Manutenzione / Nuove costruzioni |
| `Impresa` | ANAS | Empresa(s) ejecutora(s) |
| `Importo_Principale` | ANAS | Importe trabajos principales (€) |
| `Importo_Totale` | ANAS | Importe total del contrato (€) |
| `Data_Consegna_Impresa` | ANAS | Fecha de entrega a la empresa |
| `Avanzamento_Lavori` | ANAS | Porcentaje de avance de obra |
| `Data_Ultimazione_Prevista` | ANAS | Fecha prevista de finalización |
| `Dal_Km` | ANAS | Kilómetro de inicio del tramo |
| `Al_Km` | ANAS | Kilómetro de fin del tramo |
| `Strade_Segmentos` | ANAS | Todos los tramos si hay varios |
| `Coordinate` | ANAS / OSM | Coordenadas GPS: `lat, lng` |
| `Nome_Ufficiale_Progetto` | OpenCUP | Título oficial del proyecto en el registro CUP |
| `Anno_Decisione` | OpenCUP | Año de aprobación del proyecto |
| `Provincia` | OpenCUP | Provincia exacta de la obra |
| `Municipi_Coinvolti` | OpenCUP | Municipios afectados |
| `Tipologia` | OpenCUP | Tipología oficial (ej: Manutenzione Straordinaria) |
| `Settore` | OpenCUP | Sector (ej: Infrastrutture di Trasporto) |
| `Sottosettore` | OpenCUP | Subsector (ej: Stradali) |
| `Categoria_Settore` | OpenCUP | Categoría (ej: Strade Statali) |
| `Cup_Padre` | OpenCUP | CUP del proyecto paraguas |
| `Progetti_Collegati_CUP` | OpenCUP | Nº de proyectos vinculados al CUP |

**Coordenadas:** se usan las de ANAS cuando están disponibles; si no, se geocodifican automáticamente via [Nominatim (OpenStreetMap)](https://nominatim.openstreetmap.org) usando el municipio y provincia de OpenCUP.

---

## Estructura del proyecto

```
Gorima Webscrapping/
├── scraper.py          # Script principal
├── requirements.txt    # Dependencias Python
├── README.md
├── .gitignore
└── data/               # Generado al correr el scraper (no en el repo)
    ├── raw/            # JSON consolidado por ejecución
    ├── processed/      # CSV final con todas las columnas
    └── runs/           # JSON por región + metadata por ejecución
```

---

## Instalación

```bash
pip install -r requirements.txt
```

---

## Uso

```bash
python scraper.py
```

El scraper trabaja en 4 fases:

1. **ANAS** — descarga obras por región y carretera
2. **OpenCUP** — enriquece cada CUP único con datos del registro oficial
3. **Geocodificación** — obtiene coordenadas GPS para obras sin localización ANAS
4. **Exportación** — genera CSV en `data/processed/` y JSON raw en `data/raw/`

Cada ejecución genera archivos con **timestamp** — al correr el scraper periódicamente se acumula un histórico completo.

---

## APIs utilizadas

| Fuente | URL | Autenticación |
|---|---|---|
| ANAS Lavori in Corso | `stradeanas.it/it/anas_lavori_in_corso/getlavori` | No requerida |
| OpenCUP | `opencup.gov.it/progetto/-/cup/{CUP}` | No requerida |
| Nominatim (OSM) | `nominatim.openstreetmap.org/search` | No requerida |

---

## Dependencias

```
requests
pandas
beautifulsoup4
lxml
openpyxl
```

---

## Notas

- Los datos de ANAS se actualizan periódicamente — se recomienda correr el scraper cada 2-4 semanas
- OpenCUP puede devolver campos vacíos para proyectos antiguos o con datos incompletos
- Nominatim tiene un límite de 1 request/segundo — el scraper lo respeta automáticamente
- Los archivos CSV y JSON **no se suben al repositorio** (ver `.gitignore`)
