# Silent Sound - Gestor Bluetooth Desktop (Python)

Aplicacion de escritorio en Python para Windows que permite:

- Escanear dispositivos Bluetooth BLE cercanos
- Ver dispositivos ordenados por cercania (RSSI)
- Conectarse y desconectarse desde tu PC
- Filtrar por intensidad de senal para ver los mas cercanos

## Requisitos

- Windows 10 o superior
- Adaptador Bluetooth con soporte BLE
- Python 3.10+

## Instalacion

1. Abre una terminal en la carpeta del proyecto.
2. Crea un entorno virtual (opcional pero recomendado).
3. Instala dependencias:

```bash
pip install -r requirements.txt
```

## Ejecucion

```bash
python app.py
```

## Notas importantes

- Esta app usa `bleak`, por lo que el escaneo y conexion aplican a dispositivos **BLE**.
- Equipos de audio clasico (Bluetooth Classic) pueden no aparecer o no ser controlables por BLE.
- No siempre es posible forzar la desconexion de un dispositivo que este enlazado activamente con otro telefono o PC. Eso depende del dispositivo, el perfil Bluetooth y permisos del sistema.

## Permisos y problemas comunes

- Verifica que el Bluetooth de Windows este encendido.
- Si no aparecen dispositivos, asegurate de que esten en modo visible/emparejamiento.
- Si falla la conexion, prueba primero emparejar desde Configuracion de Windows > Bluetooth.
