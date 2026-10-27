import asyncio
import ctypes
import json
import re
import subprocess
import threading
import tkinter as tk
from queue import Empty, Queue
from tkinter import scrolledtext
from tkinter import ttk
from typing import Any
from ctypes import wintypes

from bleak import BleakClient, BleakScanner


class BluetoothApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Silent Sound - Gestor Bluetooth")
        self.root.geometry("980x620")

        self.queue: Queue[tuple[str, Any, Any]] = Queue()
        self.last_scan: list[dict[str, Any]] = []
        self.clients: dict[str, BleakClient] = {}
        self.scan_in_progress = False
        self.stop_scan_requested = threading.Event()
        self.scan_duration_seconds = 120

        self.status_var = tk.StringVar(value="Listo")
        self.nearby_only_var = tk.BooleanVar(value=False)
        self.rssi_threshold_var = tk.IntVar(value=-90)
        self.scan_progress_var = tk.DoubleVar(value=0.0)
        self.scan_progress_text_var = tk.StringVar(value="Escaneo inactivo")

        self._build_ui()
        self._poll_queue()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        container = ttk.Frame(self.root, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(
            container,
            text="Administrador Bluetooth BLE",
            font=("Segoe UI", 15, "bold"),
        )
        title.pack(anchor=tk.W)

        subtitle = ttk.Label(
            container,
            text=(
                "Escanea, estima distancia en metros y gestiona conexiones BLE desde este equipo."
            ),
        )
        subtitle.pack(anchor=tk.W, pady=(0, 10))

        controls = ttk.Frame(container)
        controls.pack(fill=tk.X, pady=(0, 10))

        ttk.Button(controls, text="Escanear", command=self.start_scan).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        self.stop_scan_button = ttk.Button(
            controls,
            text="Detener escaneo",
            command=self.stop_scan,
            state=tk.DISABLED,
        )
        self.stop_scan_button.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(controls, text="Conectar", command=self.connect_selected).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(controls, text="Desconectar", command=self.disconnect_selected).pack(
            side=tk.LEFT, padx=(0, 8)
        )
        ttk.Button(controls, text="Desconectar todos", command=self.disconnect_all).pack(
            side=tk.LEFT, padx=(0, 8)
        )

        ttk.Checkbutton(
            controls,
            text="Solo cercanos",
            variable=self.nearby_only_var,
            command=self._refresh_table,
        ).pack(side=tk.LEFT, padx=(16, 8))

        ttk.Label(controls, text="Umbral RSSI:").pack(side=tk.LEFT)
        threshold = ttk.Spinbox(
            controls,
            from_=-100,
            to_=-30,
            increment=1,
            width=6,
            textvariable=self.rssi_threshold_var,
            command=self._refresh_table,
        )
        threshold.pack(side=tk.LEFT, padx=(4, 8))
        threshold.bind("<Return>", lambda _event: self._refresh_table())
        threshold.bind("<FocusOut>", lambda _event: self._refresh_table())

        progress_frame = ttk.Frame(container)
        progress_frame.pack(fill=tk.X, pady=(0, 10))

        self.scan_progress = ttk.Progressbar(
            progress_frame,
            orient=tk.HORIZONTAL,
            mode="determinate",
            maximum=self.scan_duration_seconds,
            variable=self.scan_progress_var,
        )
        self.scan_progress.pack(fill=tk.X)

        progress_text = ttk.Label(
            progress_frame,
            textvariable=self.scan_progress_text_var,
        )
        progress_text.pack(anchor=tk.W, pady=(4, 0))

        table_frame = ttk.Frame(container)
        table_frame.pack(fill=tk.BOTH, expand=True)

        columns = (
            "name",
            "address",
            "rssi",
            "proximity",
            "occupied",
            "connected",
        )
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )

        self.tree.heading("name", text="Nombre")
        self.tree.heading("address", text="Direccion")
        self.tree.heading("rssi", text="RSSI")
        self.tree.heading("proximity", text="Distancia (m)")
        self.tree.heading("occupied", text="Ocupado")
        self.tree.heading("connected", text="Estado")

        self.tree.column("name", width=220, anchor=tk.W)
        self.tree.column("address", width=220, anchor=tk.W)
        self.tree.column("rssi", width=80, anchor=tk.CENTER)
        self.tree.column("proximity", width=120, anchor=tk.CENTER)
        self.tree.column("occupied", width=120, anchor=tk.CENTER)
        self.tree.column("connected", width=120, anchor=tk.CENTER)

        y_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=y_scroll.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        y_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        info = ttk.Label(
            container,
            text=(
                "Nota: no siempre es posible expulsar un dispositivo conectado a otro telefono/PC."
            ),
            foreground="#7A5200",
        )
        info.pack(fill=tk.X, pady=(10, 4))

        status_bar = ttk.Label(
            container,
            textvariable=self.status_var,
            relief=tk.SUNKEN,
            anchor=tk.W,
            padding=(8, 4),
        )
        status_bar.pack(fill=tk.X)

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload, callback = self.queue.get_nowait()
                if kind == "ok":
                    callback(payload)
                elif kind == "err":
                    callback(payload)
        except Empty:
            pass
        finally:
            self.root.after(120, self._poll_queue)

    def _run_async(self, coro: Any, on_ok: Any, on_err: Any) -> None:
        def worker() -> None:
            try:
                result = asyncio.run(coro)
                self.queue.put(("ok", result, on_ok))
            except Exception as exc:  # noqa: BLE001
                self.queue.put(("err", exc, on_err))

        threading.Thread(target=worker, daemon=True).start()

    def start_scan(self) -> None:
        if self.scan_in_progress:
            self.status_var.set("Ya hay un escaneo en progreso...")
            return

        self.stop_scan_requested.clear()
        self.scan_in_progress = True
        self.last_scan = []
        self.stop_scan_button.configure(state=tk.NORMAL)
        self._refresh_table()
        self._set_scan_progress(0)
        self.status_var.set(
            "Escaneando dispositivos BLE durante 2 minutos (modo alta sensibilidad)..."
        )
        self._run_async(
            self._scan_ble_devices(),
            self._on_scan_ok,
            self._on_scan_error,
        )

    def stop_scan(self) -> None:
        if not self.scan_in_progress:
            self.status_var.set("No hay un escaneo activo para detener.")
            return

        self.stop_scan_requested.set()
        self.status_var.set("Deteniendo escaneo...")

    async def _scan_ble_devices(self) -> list[dict[str, Any]]:
        rows_by_address: dict[str, dict[str, Any]] = {}
        known_addresses = set(rows_by_address.keys())
        newly_found = 0
        windows_name_map = self._get_windows_bluetooth_name_map()

        def detection_callback(device: Any, advertisement_data: Any) -> None:
            nonlocal newly_found
            address = device.address
            if address in known_addresses:
                return

            # En Windows suele ser mas confiable leer RSSI desde advertising data.
            adv_rssi = getattr(advertisement_data, "rssi", None)
            dev_rssi = getattr(device, "rssi", None)
            rssi = adv_rssi if isinstance(adv_rssi, int) else dev_rssi
            connected = False
            existing = self.clients.get(address)
            if existing is not None:
                connected = existing.is_connected

            resolved_name = self._resolve_device_name(
                device,
                advertisement_data,
                windows_name_map,
            )

            row = {
                "name": resolved_name,
                "address": address,
                "rssi": rssi,
                "connected": connected,
                "source": "BLE",
                "possibly_occupied": False,
                "occupation_note": "Sin diagnostico de ocupacion.",
                "local_name": getattr(advertisement_data, "local_name", None),
                "service_uuids": list(getattr(advertisement_data, "service_uuids", []) or []),
                "manufacturer_data": dict(
                    getattr(advertisement_data, "manufacturer_data", {}) or {}
                ),
                "tx_power": getattr(advertisement_data, "tx_power", None),
                "platform_data": repr(getattr(advertisement_data, "platform_data", None)),
                "device_details": repr(getattr(device, "details", None)),
            }
            known_addresses.add(address)
            newly_found += 1
            rows_by_address[address] = row
            self.queue.put(("ok", row, self._on_scan_device_found))

        scanner = BleakScanner(
            detection_callback=detection_callback,
            scanning_mode="active",
        )
        await scanner.start()

        try:
            for elapsed in range(1, self.scan_duration_seconds + 1):
                if self.stop_scan_requested.is_set():
                    break
                await asyncio.sleep(1)
                self.queue.put(("ok", elapsed, self._set_scan_progress))
        finally:
            await scanner.stop()

        rows = list(rows_by_address.values())

        classic_rows = await asyncio.to_thread(self._scan_classic_devices_windows)
        classic_added = 0
        for classic in classic_rows:
            existing = rows_by_address.get(classic["address"])
            if existing is None:
                rows_by_address[classic["address"]] = classic
                classic_added += 1
            else:
                if existing.get("name") == "Desconocido" and classic.get("name"):
                    existing["name"] = classic["name"]
                existing["classic_connected"] = classic.get("classic_connected")
                existing["classic_paired"] = classic.get("classic_paired")
                existing["classic_authenticated"] = classic.get("classic_authenticated")
                existing["source"] = "BLE + Clasico"

        rows = list(rows_by_address.values())
        rows.sort(
            key=lambda x: x["rssi"] if isinstance(x["rssi"], int) else -999,
            reverse=True,
        )
        self.queue.put(("ok", newly_found, self._on_new_devices_count))
        if classic_added:
            self.queue.put(("ok", classic_added, self._on_classic_devices_count))
        return rows

    def _on_classic_devices_count(self, classic_added: int) -> None:
        self.status_var.set(
            f"Escaneo en curso. Dispositivos Bluetooth clasico agregados: {classic_added}."
        )

    def _scan_classic_devices_windows(self) -> list[dict[str, Any]]:
        # Descubrimiento de dispositivos Bluetooth clasico (BR/EDR) con API nativa de Windows.
        class SYSTEMTIME(ctypes.Structure):
            _fields_ = [
                ("wYear", wintypes.WORD),
                ("wMonth", wintypes.WORD),
                ("wDayOfWeek", wintypes.WORD),
                ("wDay", wintypes.WORD),
                ("wHour", wintypes.WORD),
                ("wMinute", wintypes.WORD),
                ("wSecond", wintypes.WORD),
                ("wMilliseconds", wintypes.WORD),
            ]

        class BLUETOOTH_ADDRESS(ctypes.Structure):
            _fields_ = [("rgBytes", ctypes.c_ubyte * 6)]

        class BLUETOOTH_DEVICE_INFO(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("Address", BLUETOOTH_ADDRESS),
                ("ulClassofDevice", wintypes.ULONG),
                ("fConnected", wintypes.BOOL),
                ("fRemembered", wintypes.BOOL),
                ("fAuthenticated", wintypes.BOOL),
                ("stLastSeen", SYSTEMTIME),
                ("stLastUsed", SYSTEMTIME),
                ("szName", wintypes.WCHAR * 248),
            ]

        class BLUETOOTH_DEVICE_SEARCH_PARAMS(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("fReturnAuthenticated", wintypes.BOOL),
                ("fReturnRemembered", wintypes.BOOL),
                ("fReturnUnknown", wintypes.BOOL),
                ("fReturnConnected", wintypes.BOOL),
                ("fIssueInquiry", wintypes.BOOL),
                ("cTimeoutMultiplier", ctypes.c_ubyte),
                ("hRadio", wintypes.HANDLE),
            ]

        devices: list[dict[str, Any]] = []

        try:
            btapi = ctypes.windll.BluetoothAPIs
        except Exception:
            return devices

        btapi.BluetoothFindFirstDevice.argtypes = [
            ctypes.POINTER(BLUETOOTH_DEVICE_SEARCH_PARAMS),
            ctypes.POINTER(BLUETOOTH_DEVICE_INFO),
        ]
        btapi.BluetoothFindFirstDevice.restype = wintypes.HANDLE
        btapi.BluetoothFindNextDevice.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(BLUETOOTH_DEVICE_INFO),
        ]
        btapi.BluetoothFindNextDevice.restype = wintypes.BOOL
        btapi.BluetoothFindDeviceClose.argtypes = [wintypes.HANDLE]
        btapi.BluetoothFindDeviceClose.restype = wintypes.BOOL

        params = BLUETOOTH_DEVICE_SEARCH_PARAMS()
        params.dwSize = ctypes.sizeof(BLUETOOTH_DEVICE_SEARCH_PARAMS)
        params.fReturnAuthenticated = True
        params.fReturnRemembered = True
        params.fReturnUnknown = True
        params.fReturnConnected = True
        params.fIssueInquiry = True
        params.cTimeoutMultiplier = 2
        params.hRadio = None

        info = BLUETOOTH_DEVICE_INFO()
        info.dwSize = ctypes.sizeof(BLUETOOTH_DEVICE_INFO)

        try:
            h_find = btapi.BluetoothFindFirstDevice(ctypes.byref(params), ctypes.byref(info))
        except Exception:
            return devices

        if not h_find:
            return devices

        try:
            while True:
                addr_bytes = list(info.Address.rgBytes)
                address = ":".join(f"{b:02X}" for b in reversed(addr_bytes))
                name = str(info.szName).strip() or "Desconocido"

                devices.append(
                    {
                        "name": name,
                        "address": address,
                        "rssi": None,
                        "connected": False,
                        "source": "Clasico",
                        "classic_connected": bool(info.fConnected),
                        "classic_paired": bool(info.fRemembered),
                        "classic_authenticated": bool(info.fAuthenticated),
                        "possibly_occupied": False,
                        "occupation_note": "Sin diagnostico de ocupacion.",
                        "local_name": None,
                        "service_uuids": [],
                        "manufacturer_data": {},
                        "tx_power": None,
                        "platform_data": "",
                        "device_details": "",
                    }
                )

                info = BLUETOOTH_DEVICE_INFO()
                info.dwSize = ctypes.sizeof(BLUETOOTH_DEVICE_INFO)
                has_next = btapi.BluetoothFindNextDevice(h_find, ctypes.byref(info))
                if not has_next:
                    break
        finally:
            btapi.BluetoothFindDeviceClose(h_find)

        return devices

    @staticmethod
    def _normalize_address(address: str | None) -> str:
        if not address:
            return ""
        compact = "".join(ch for ch in address.upper() if ch in "0123456789ABCDEF")
        if len(compact) != 12:
            return ""
        return ":".join(compact[i : i + 2] for i in range(0, 12, 2))

    def _resolve_device_name(
        self,
        device: Any,
        advertisement_data: Any,
        windows_name_map: dict[str, str],
    ) -> str:
        candidates: list[str] = []

        candidates.append(str(getattr(advertisement_data, "local_name", "") or ""))
        candidates.append(str(getattr(device, "name", "") or ""))

        details = getattr(device, "details", None)
        if details is not None:
            candidates.append(str(getattr(details, "name", "") or ""))

        normalized_address = self._normalize_address(getattr(device, "address", None))
        if normalized_address:
            candidates.append(windows_name_map.get(normalized_address, ""))

        for candidate in candidates:
            clean = candidate.strip()
            if clean and clean.lower() not in {"unknown", "desconocido"}:
                return clean

        return "Desconocido"

    def _get_windows_bluetooth_name_map(self) -> dict[str, str]:
        command = (
            "Get-PnpDevice -Class Bluetooth "
            "| Select-Object FriendlyName,InstanceId "
            "| ConvertTo-Json -Depth 3"
        )

        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", command],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except Exception:
            return {}

        raw = (completed.stdout or "").strip()
        if not raw:
            return {}

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}

        items = parsed if isinstance(parsed, list) else [parsed]
        name_map: dict[str, str] = {}

        for item in items:
            if not isinstance(item, dict):
                continue

            friendly_name = str(item.get("FriendlyName") or "").strip()
            instance_id = str(item.get("InstanceId") or "")

            if not friendly_name or not instance_id:
                continue

            match = re.search(r"DEV_([0-9A-Fa-f]{12})", instance_id)
            if not match:
                continue

            hex_address = match.group(1).upper()
            normalized = ":".join(
                hex_address[i : i + 2] for i in range(0, len(hex_address), 2)
            )
            if normalized:
                name_map[normalized] = friendly_name

        return name_map

    def _on_new_devices_count(self, newly_found: int) -> None:
        self.status_var.set(
            f"Escaneo en curso. Nuevos dispositivos encontrados en esta ronda: {newly_found}."
        )

    def _set_scan_progress(self, elapsed: int) -> None:
        safe_elapsed = max(0, min(elapsed, self.scan_duration_seconds))
        self.scan_progress_var.set(safe_elapsed)
        remaining = self.scan_duration_seconds - safe_elapsed
        self.scan_progress_text_var.set(
            f"Escaneo: {safe_elapsed}s / {self.scan_duration_seconds}s | Restante: {remaining}s"
        )

    def _on_scan_device_found(self, row: dict[str, Any]) -> None:
        self._upsert_scanned_device(row)
        self._refresh_table()

    def _on_scan_ok(self, rows: list[dict[str, Any]]) -> None:
        was_stopped = self.stop_scan_requested.is_set()
        self.scan_in_progress = False
        self.stop_scan_requested.clear()
        self.stop_scan_button.configure(state=tk.DISABLED)
        self.last_scan = rows
        if not was_stopped:
            self._set_scan_progress(self.scan_duration_seconds)
        self._refresh_table()
        if was_stopped:
            self.status_var.set(
                f"Escaneo detenido por el usuario. {len(rows)} dispositivo(s) BLE en la lista."
            )
        elif rows:
            self.status_var.set(
                f"Escaneo completado. {len(rows)} dispositivo(s) BLE detectado(s)."
            )
        else:
            self.status_var.set(
                "No se detectaron dispositivos BLE. Activa modo emparejamiento y recuerda que Bluetooth clasico puede no aparecer."
            )

    def _on_scan_error(self, exc: Exception) -> None:
        self.scan_in_progress = False
        self.stop_scan_requested.clear()
        self.stop_scan_button.configure(state=tk.DISABLED)
        self.scan_progress_text_var.set("Escaneo fallido")
        self.status_var.set(f"Error durante el escaneo: {exc}")

    def _refresh_table(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

        rows = self.last_scan[:]

        if self.nearby_only_var.get():
            threshold = self.rssi_threshold_var.get()
            rows = [
                r
                for r in rows
                if isinstance(r["rssi"], int) and r["rssi"] >= threshold
            ]

        for r in rows:
            rssi = r["rssi"] if r["rssi"] is not None else "N/A"
            distance_label = self._distance_label(r["rssi"])
            occupied = "Posible" if r.get("possibly_occupied") else "No"
            state = "Conectado" if r["connected"] else "No conectado"
            self.tree.insert(
                "",
                tk.END,
                iid=r["address"],
                values=(r["name"], r["address"], rssi, distance_label, occupied, state),
            )

    @staticmethod
    def _rssi_to_meters(
        rssi: Any,
        measured_at_one_meter: int = -59,
        environment_factor: float = 3.0,
    ) -> float | None:
        if not isinstance(rssi, int):
            return None
        if environment_factor <= 0:
            return None
        distance = 10 ** ((measured_at_one_meter - rssi) / (10 * environment_factor))
        return round(distance, 2)

    def _distance_label(self, rssi: Any) -> str:
        meters = self._rssi_to_meters(rssi)
        if meters is None:
            return "N/A"
        return f"{meters:.2f}"

    @staticmethod
    def _proximity_label(rssi: Any) -> str:
        if not isinstance(rssi, int):
            return "Desconocida"
        if rssi >= -55:
            return "Muy cerca"
        if rssi >= -67:
            return "Cerca"
        if rssi >= -78:
            return "Media"
        return "Lejos"

    def _selected_address(self) -> str | None:
        selected = self.tree.selection()
        if not selected:
            return None
        return selected[0]

    def _on_tree_double_click(self, _event: Any) -> None:
        address = self._selected_address()
        if not address:
            focus = self.tree.focus()
            address = str(focus) if focus else None
        if not address:
            self.status_var.set("Selecciona un dispositivo para ver detalles.")
            return
        self._open_device_details_modal(address)

    def _open_device_details_modal(self, address: str) -> None:
        device = self._find_device(address)
        name = (device or {}).get("name") or "Desconocido"

        modal = tk.Toplevel(self.root)
        modal.title(f"Detalles BLE - {name}")
        modal.geometry("860x620")
        modal.transient(self.root)
        modal.grab_set()

        header = ttk.Frame(modal, padding=(12, 10))
        header.pack(fill=tk.X)

        ttk.Label(
            header,
            text=f"{name}",
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor=tk.W)
        ttk.Label(
            header,
            text=f"Direccion: {address}",
        ).pack(anchor=tk.W, pady=(2, 8))

        modal_status_var = tk.StringVar(value="Recopilando informacion avanzada del dispositivo...")
        ttk.Label(
            header,
            textvariable=modal_status_var,
            foreground="#1E4A7A",
        ).pack(anchor=tk.W)

        details_text = scrolledtext.ScrolledText(
            modal,
            wrap=tk.WORD,
            height=25,
            font=("Consolas", 10),
        )
        details_text.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 10))
        details_text.insert("1.0", "Cargando datos...\n")
        details_text.configure(state=tk.DISABLED)

        actions = ttk.Frame(modal, padding=(12, 0, 12, 12))
        actions.pack(fill=tk.X)

        ttk.Button(
            actions,
            text="Actualizar detalles",
            command=lambda: self._load_modal_device_details(address, details_text, modal_status_var),
        ).pack(side=tk.LEFT)

        ttk.Button(
            actions,
            text="Desconectar conexion vigente",
            command=lambda: self._disconnect_from_modal(address, modal_status_var, details_text),
        ).pack(side=tk.LEFT, padx=(8, 0))

        ttk.Button(
            actions,
            text="Cerrar",
            command=modal.destroy,
        ).pack(side=tk.RIGHT)

        self._load_modal_device_details(address, details_text, modal_status_var)

    def _find_device(self, address: str) -> dict[str, Any] | None:
        for row in self.last_scan:
            if row["address"] == address:
                return row
        return None

    def _load_modal_device_details(
        self,
        address: str,
        details_text: scrolledtext.ScrolledText,
        modal_status_var: tk.StringVar,
    ) -> None:
        modal_status_var.set("Consultando informacion BLE del dispositivo...")
        self._run_async(
            self._collect_device_details(address),
            lambda details: self._on_modal_details_ok(details, details_text, modal_status_var),
            lambda exc: self._on_modal_details_error(exc, details_text, modal_status_var),
        )

    async def _collect_device_details(self, address: str) -> dict[str, Any]:
        info: dict[str, Any] = {
            "address": address,
            "known_device": self._find_device(address),
            "app_connected": False,
            "connection_note": "Sin diagnostico de conexion.",
            "possibly_occupied": False,
            "mtu": None,
            "services": [],
            "service_count": 0,
            "characteristic_count": 0,
            "descriptor_count": 0,
            "battery_level": None,
            "device_name_gatt": None,
            "errors": [],
        }

        active_client = self.clients.get(address)
        used_temp_client = False
        temp_client: BleakClient | None = None
        client: BleakClient | None = None

        if active_client is not None and active_client.is_connected:
            client = active_client
            info["app_connected"] = True
            info["connection_note"] = "Conectado desde esta aplicacion."
        else:
            temp_client = BleakClient(address, timeout=12.0)
            client = temp_client
            used_temp_client = True

            try:
                await client.connect()
                info["connection_note"] = (
                    "El dispositivo acepto conexion durante el diagnostico."
                )
            except Exception as exc:  # noqa: BLE001
                msg = str(exc)
                lowered = msg.lower()
                if any(
                    token in lowered
                    for token in (
                        "in use",
                        "busy",
                        "access denied",
                        "already connected",
                        "currently connected",
                    )
                ):
                    info["possibly_occupied"] = True
                    info["connection_note"] = (
                        "Posible conexion activa en otro dispositivo o bloqueo del sistema operativo."
                    )
                else:
                    info["connection_note"] = "No se pudo abrir conexion BLE para diagnostico."
                info["errors"].append(f"Conexion diagnostico: {msg}")

        try:
            if client is not None and client.is_connected:
                info["mtu"] = getattr(client, "mtu_size", None)

                services = client.services
                if services is None:
                    services = await client.get_services()

                for service in services:
                    service_entry = {
                        "uuid": service.uuid,
                        "description": service.description,
                        "characteristics": [],
                    }
                    info["service_count"] += 1

                    for char in service.characteristics:
                        descriptors = []
                        for descriptor in char.descriptors:
                            descriptors.append(
                                {
                                    "handle": descriptor.handle,
                                    "uuid": descriptor.uuid,
                                }
                            )
                            info["descriptor_count"] += 1

                        char_entry = {
                            "uuid": char.uuid,
                            "description": char.description,
                            "handle": char.handle,
                            "properties": list(char.properties),
                            "descriptors": descriptors,
                        }
                        service_entry["characteristics"].append(char_entry)
                        info["characteristic_count"] += 1

                    info["services"].append(service_entry)

                try:
                    device_name_bytes = await client.read_gatt_char(
                        "00002a00-0000-1000-8000-00805f9b34fb"
                    )
                    info["device_name_gatt"] = device_name_bytes.decode(
                        "utf-8", errors="ignore"
                    ).strip()
                except Exception as exc:  # noqa: BLE001
                    info["errors"].append(f"Lectura nombre GATT (2A00): {exc}")

                try:
                    battery_raw = await client.read_gatt_char(
                        "00002a19-0000-1000-8000-00805f9b34fb"
                    )
                    if battery_raw:
                        info["battery_level"] = int(battery_raw[0])
                except Exception as exc:  # noqa: BLE001
                    info["errors"].append(f"Lectura bateria (2A19): {exc}")
        finally:
            if used_temp_client and temp_client is not None and temp_client.is_connected:
                try:
                    await temp_client.disconnect()
                except Exception as exc:  # noqa: BLE001
                    info["errors"].append(f"Cierre conexion temporal: {exc}")

        return info

    def _on_modal_details_ok(
        self,
        details: dict[str, Any],
        details_text: scrolledtext.ScrolledText,
        modal_status_var: tk.StringVar,
    ) -> None:
        self._update_occupation_diagnosis(
            details.get("address", ""),
            bool(details.get("possibly_occupied")),
            str(details.get("connection_note", "Sin diagnostico de conexion.")),
        )
        self._refresh_table()

        rendered = self._render_device_details(details)
        details_text.configure(state=tk.NORMAL)
        details_text.delete("1.0", tk.END)
        details_text.insert("1.0", rendered)
        details_text.configure(state=tk.DISABLED)
        modal_status_var.set("Informacion actualizada.")

    def _update_occupation_diagnosis(
        self,
        address: str,
        possibly_occupied: bool,
        note: str,
    ) -> None:
        if not address:
            return
        row = self._find_device(address)
        if row is None:
            return
        row["possibly_occupied"] = possibly_occupied
        row["occupation_note"] = note

    def _on_modal_details_error(
        self,
        exc: Exception,
        details_text: scrolledtext.ScrolledText,
        modal_status_var: tk.StringVar,
    ) -> None:
        details_text.configure(state=tk.NORMAL)
        details_text.delete("1.0", tk.END)
        details_text.insert("1.0", f"Error consultando detalles: {exc}\n")
        details_text.configure(state=tk.DISABLED)
        modal_status_var.set("No fue posible cargar la informacion avanzada.")

    def _render_device_details(self, details: dict[str, Any]) -> str:
        known = details.get("known_device") or {}

        lines: list[str] = []
        lines.append("=== DATOS BASICOS ===")
        lines.append(f"Nombre (scan): {known.get('name', 'Desconocido')}")
        lines.append(f"Direccion: {details.get('address', 'N/A')}")
        lines.append(f"RSSI: {known.get('rssi', 'N/A')}")
        lines.append(
            f"Distancia estimada: {self._distance_label(known.get('rssi'))} m"
        )
        lines.append(f"Cercania estimada: {self._proximity_label(known.get('rssi'))}")
        lines.append(f"Nombre local anuncio: {known.get('local_name', 'N/A')}")
        lines.append(f"Fuente deteccion: {known.get('source', 'N/A')}")
        lines.append(f"Nombre GATT (2A00): {details.get('device_name_gatt', 'N/A')}")
        tx_power = known.get("tx_power")
        lines.append(f"TX Power (anuncio): {tx_power if tx_power is not None else 'N/A'}")
        lines.append(
            f"Clasico conectado: {'Si' if known.get('classic_connected') else 'No'}"
        )
        lines.append(
            f"Clasico emparejado: {'Si' if known.get('classic_paired') else 'No'}"
        )
        lines.append("")

        lines.append("=== ESTADO DE CONEXION ===")
        lines.append(
            f"Conectado desde esta app: {'Si' if details.get('app_connected') else 'No'}"
        )
        lines.append(
            "Posible ocupacion por otro equipo: "
            f"{'Si' if details.get('possibly_occupied') else 'No'}"
        )
        lines.append(f"Diagnostico: {details.get('connection_note', 'N/A')}")
        lines.append(
            "Nota: por limitaciones BLE/Windows no siempre se puede confirmar ni forzar una desconexion de enlaces con otros equipos."
        )
        lines.append("")

        lines.append("=== ANUNCIO BLE ===")
        uuids = known.get("service_uuids") or []
        lines.append(f"Service UUIDs anunciados: {len(uuids)}")
        for uuid in uuids:
            lines.append(f"  - {uuid}")

        manufacturer_data = known.get("manufacturer_data") or {}
        lines.append(f"Manufacturer data entries: {len(manufacturer_data)}")
        for company_id, raw_value in manufacturer_data.items():
            if isinstance(raw_value, bytes):
                payload = raw_value.hex(" ")
            else:
                payload = str(raw_value)
            lines.append(f"  - Company ID {company_id}: {payload}")

        lines.append(f"Platform data: {known.get('platform_data', 'N/A')}")
        lines.append(f"Device details object: {known.get('device_details', 'N/A')}")
        lines.append("")

        lines.append("=== GATT ===")
        lines.append(f"MTU: {details.get('mtu', 'N/A')}")
        battery = details.get("battery_level")
        lines.append(
            f"Bateria (2A19): {str(battery) + '%' if isinstance(battery, int) else 'N/A'}"
        )
        lines.append(f"Servicios: {details.get('service_count', 0)}")
        lines.append(f"Caracteristicas: {details.get('characteristic_count', 0)}")
        lines.append(f"Descriptores: {details.get('descriptor_count', 0)}")

        services = details.get("services") or []
        for service in services:
            lines.append(f"\n[Servicio] {service.get('uuid')} | {service.get('description')}")
            for char in service.get("characteristics", []):
                props = ", ".join(char.get("properties", []))
                lines.append(
                    f"  [Char] {char.get('uuid')} | {char.get('description')} | Handle {char.get('handle')}"
                )
                lines.append(f"    Propiedades: {props}")
                for descriptor in char.get("descriptors", []):
                    lines.append(
                        f"    [Desc] {descriptor.get('uuid')} | Handle {descriptor.get('handle')}"
                    )

        errors = details.get("errors") or []
        if errors:
            lines.append("\n=== ERRORES/RESTRICCIONES ===")
            for err in errors:
                lines.append(f"- {err}")

        lines.append("")
        return "\n".join(lines)

    def _disconnect_from_modal(
        self,
        address: str,
        modal_status_var: tk.StringVar,
        details_text: scrolledtext.ScrolledText,
    ) -> None:
        modal_status_var.set("Intentando desconectar la conexion vigente del dispositivo...")
        self._run_async(
            self._disconnect_any_connection(address),
            lambda message: self._on_modal_disconnect_ok(
                address,
                message,
                modal_status_var,
                details_text,
            ),
            lambda exc: modal_status_var.set(f"Fallo al desconectar: {exc}"),
        )

    async def _disconnect_any_connection(self, address: str) -> str:
        client = self.clients.get(address)
        if client is not None and client.is_connected:
            await client.disconnect()
            return "Se desconecto la conexion activa de esta aplicacion."

        temp_client = BleakClient(address, timeout=10.0)
        try:
            await temp_client.connect()
            await temp_client.disconnect()
            return (
                "Se realizo una conexion temporal y desconexion local. "
                "Si existia un enlace con otro equipo, puede que no sea desconectable desde aqui."
            )
        except Exception as exc:  # noqa: BLE001
            return (
                "No fue posible forzar una desconexion remota. "
                f"Motivo: {exc}"
            )
        finally:
            if temp_client.is_connected:
                try:
                    await temp_client.disconnect()
                except Exception:
                    pass

    def _on_modal_disconnect_ok(
        self,
        address: str,
        message: str,
        modal_status_var: tk.StringVar,
        details_text: scrolledtext.ScrolledText,
    ) -> None:
        self._update_scanned_device_state(address, False)
        self._refresh_table()
        modal_status_var.set(message)
        self._load_modal_device_details(address, details_text, modal_status_var)

    def connect_selected(self) -> None:
        address = self._selected_address()
        if not address:
            self.status_var.set("Selecciona un dispositivo para conectar.")
            return

        self.status_var.set(f"Conectando a {address}...")
        self._run_async(
            self._connect_device(address),
            lambda ok: self._on_connect_ok(address, ok),
            lambda exc: self.status_var.set(f"Error al conectar {address}: {exc}"),
        )

    async def _connect_device(self, address: str) -> bool:
        client = self.clients.get(address)
        if client is None:
            client = BleakClient(address, timeout=15.0)
            self.clients[address] = client

        if not client.is_connected:
            await client.connect()

        return client.is_connected

    def _on_connect_ok(self, address: str, ok: bool) -> None:
        self._update_scanned_device_state(address, ok)
        self._refresh_table()
        if ok:
            self.status_var.set(f"Conectado a {address}.")
        else:
            self.status_var.set(f"No se pudo conectar a {address}.")

    def disconnect_selected(self) -> None:
        address = self._selected_address()
        if not address:
            self.status_var.set("Selecciona un dispositivo para desconectar.")
            return

        self.status_var.set(f"Desconectando {address}...")
        self._run_async(
            self._disconnect_device(address),
            lambda ok: self._on_disconnect_ok(address, ok),
            lambda exc: self.status_var.set(f"Error al desconectar {address}: {exc}"),
        )

    async def _disconnect_device(self, address: str) -> bool:
        client = self.clients.get(address)
        if client is None:
            return False

        if client.is_connected:
            await client.disconnect()

        return not client.is_connected

    def _on_disconnect_ok(self, address: str, ok: bool) -> None:
        self._update_scanned_device_state(address, not ok)
        self._refresh_table()
        if ok:
            self.status_var.set(f"Desconectado {address}.")
        else:
            self.status_var.set(f"No habia conexion activa con {address}.")

    def disconnect_all(self) -> None:
        addresses = [addr for addr, c in self.clients.items() if c.is_connected]
        if not addresses:
            self.status_var.set("No hay dispositivos conectados desde esta app.")
            return

        self.status_var.set("Desconectando todos los dispositivos...")
        self._run_async(
            self._disconnect_all_devices(addresses),
            self._on_disconnect_all_ok,
            lambda exc: self.status_var.set(f"Error al desconectar todos: {exc}"),
        )

    async def _disconnect_all_devices(self, addresses: list[str]) -> dict[str, bool]:
        results: dict[str, bool] = {}
        for addr in addresses:
            try:
                ok = await self._disconnect_device(addr)
            except Exception:  # noqa: BLE001
                ok = False
            results[addr] = ok
        return results

    def _on_disconnect_all_ok(self, results: dict[str, bool]) -> None:
        disconnected = sum(1 for v in results.values() if v)
        for address, ok in results.items():
            self._update_scanned_device_state(address, not ok)
        self._refresh_table()
        self.status_var.set(f"Desconexion masiva finalizada. Exitosos: {disconnected}.")

    def _update_scanned_device_state(self, address: str, connected: bool) -> None:
        for d in self.last_scan:
            if d["address"] == address:
                d["connected"] = connected
                break

    def _upsert_scanned_device(self, row: dict[str, Any]) -> None:
        for d in self.last_scan:
            if d["address"] == row["address"]:
                d.update(row)
                break
        else:
            self.last_scan.append(row)

        self.last_scan.sort(
            key=lambda x: x["rssi"] if isinstance(x["rssi"], int) else -999,
            reverse=True,
        )

    def _on_close(self) -> None:
        # Cierra conexiones activas sin bloquear demasiado la salida.
        for client in list(self.clients.values()):
            if client.is_connected:
                try:
                    asyncio.run(client.disconnect())
                except Exception:
                    pass
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    app = BluetoothApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
