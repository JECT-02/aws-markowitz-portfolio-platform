# Portfolio Markowitz - Serverless en AWS

Optimización de portafolios con Markowitz robustecido sobre una arquitectura
serverless en AWS. El diseño completo está en `Architecture.md`.

Estado actual: Fase 0 (esqueleto del proyecto). Sin lógica de negocio, sin
infraestructura y sin credenciales.

## Requisitos

- Python 3.12 (ver `.python-version`)
- [uv](https://docs.astral.sh/uv/)
- Docker (para la imagen del optimizador, fases posteriores)
- AWS CDK (fases posteriores)

## Comandos

Usa `make` como entrada única. Ver `Makefile`.
