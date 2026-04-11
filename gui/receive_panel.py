from __future__ import annotations

import customtkinter as ctk

from windrop.gui.device_card import DeviceCard


class IncomingRequestCard(ctk.CTkFrame):
    def __init__(self, master, *, title: str, details: str, on_accept, on_decline) -> None:
        super().__init__(
            master,
            fg_color="#141A24",
            corner_radius=16,
            border_width=1,
            border_color="#263041",
        )
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)

        title_label = ctk.CTkLabel(
            self,
            text=title,
            anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        title_label.grid(row=0, column=0, padx=16, pady=(14, 4), sticky="ew")

        details_label = ctk.CTkLabel(
            self,
            text=details,
            anchor="w",
            text_color="#98A4B8",
            font=ctk.CTkFont(size=12),
        )
        details_label.grid(row=1, column=0, padx=16, pady=(0, 14), sticky="ew")

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=0, column=1, rowspan=2, padx=16, pady=12, sticky="e")

        accept = ctk.CTkButton(
            buttons,
            text="Accept",
            command=on_accept,
            width=78,
            height=34,
            corner_radius=12,
            fg_color="#D98F2B",
            hover_color="#C8801B",
            text_color="#111111",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        accept.grid(row=0, column=0, padx=(0, 8))

        decline = ctk.CTkButton(
            buttons,
            text="Decline",
            command=on_decline,
            width=78,
            height=34,
            corner_radius=12,
            fg_color="#242C3B",
            hover_color="#2D3647",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        decline.grid(row=0, column=1)


class ReceivePanel(ctk.CTkFrame):
    """Right-side panel showing nearby devices and incoming transfers."""

    def __init__(self, master, *, on_device_selected, on_incoming_action, on_manual_connect=None) -> None:
        super().__init__(master, fg_color="#10151F", corner_radius=20)
        self._on_device_selected = on_device_selected
        self._on_incoming_action = on_incoming_action
        self._on_manual_connect = on_manual_connect
        self._device_cards: dict[str, DeviceCard] = {}
        self._devices: dict[str, dict[str, str | int]] = {}
        self._selected_device_name: str | None = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(3, weight=1)

        header_row = ctk.CTkFrame(self, fg_color="transparent")
        header_row.grid(row=0, column=0, padx=24, pady=(24, 16), sticky="ew")
        header_row.grid_columnconfigure(0, weight=1)

        nearby_header = ctk.CTkLabel(
            header_row,
            text="Nearby iPhones",
            anchor="w",
            font=ctk.CTkFont(size=24, weight="bold"),
        )
        nearby_header.grid(row=0, column=0, sticky="w")

        connect_btn = ctk.CTkButton(
            header_row,
            text="+ Connect by IP",
            command=self._open_manual_connect,
            width=130,
            height=32,
            corner_radius=12,
            fg_color="#242C3B",
            hover_color="#2D3647",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        connect_btn.grid(row=0, column=1, sticky="e")

        self._nearby_list = ctk.CTkScrollableFrame(
            self,
            fg_color="#131C29",
            corner_radius=18,
            border_width=1,
            border_color="#2C3648",
            label_text="",
        )
        self._nearby_list.grid(row=1, column=0, padx=24, pady=(0, 20), sticky="nsew")
        self._nearby_list.grid_columnconfigure(0, weight=1)

        incoming_header = ctk.CTkLabel(
            self,
            text="Incoming",
            anchor="w",
            font=ctk.CTkFont(size=24, weight="bold"),
        )
        incoming_header.grid(row=2, column=0, padx=24, pady=(0, 16), sticky="ew")

        self._incoming_list = ctk.CTkScrollableFrame(
            self,
            fg_color="#131C29",
            corner_radius=18,
            border_width=1,
            border_color="#2C3648",
            label_text="",
        )
        self._incoming_list.grid(row=3, column=0, padx=24, pady=(0, 24), sticky="nsew")
        self._incoming_list.grid_columnconfigure(0, weight=1)

    def populate_devices(self, devices: list[dict[str, str]]) -> None:
        self._devices = {device["name"]: dict(device) for device in devices}
        self._render_devices()

    def add_device(self, device: dict[str, str | int]) -> None:
        self._devices[str(device["name"])] = dict(device)
        self._render_devices()

    def remove_device(self, device_name: str) -> None:
        self._devices.pop(device_name, None)
        if self._selected_device_name == device_name:
            self._selected_device_name = None
        self._render_devices()

    def get_device(self, device_name: str) -> dict[str, str | int] | None:
        device = self._devices.get(device_name)
        return dict(device) if device else None

    def _render_devices(self) -> None:
        for child in self._nearby_list.winfo_children():
            child.destroy()
        self._device_cards.clear()

        if not self._devices:
            empty = ctk.CTkLabel(
                self._nearby_list,
                text="Searching for AirDrop receivers on this Wi-Fi network...",
                justify="center",
                text_color="#98A4B8",
                font=ctk.CTkFont(size=13),
            )
            empty.grid(row=0, column=0, padx=18, pady=24)
            return

        for index, device in enumerate(self._devices.values()):
            card = DeviceCard(
                self._nearby_list,
                name=str(device["name"]),
                subtitle=str(device["subtitle"]),
                status=str(device["status"]),
                on_select=self._handle_device_select,
                selected=str(device["name"]) == self._selected_device_name,
            )
            card.grid(row=index, column=0, padx=14, pady=(14 if index == 0 else 0, 14), sticky="ew")
            self._device_cards[str(device["name"])] = card

    def populate_incoming(self, requests: list[dict[str, str]]) -> None:
        for child in self._incoming_list.winfo_children():
            child.destroy()

        if not requests:
            empty = ctk.CTkLabel(
                self._incoming_list,
                text="No incoming transfer requests.\nAccept or decline requests from nearby iPhones here.",
                justify="center",
                text_color="#98A4B8",
                font=ctk.CTkFont(size=13),
            )
            empty.grid(row=0, column=0, padx=18, pady=24)
            return

        for index, request in enumerate(requests):
            card = IncomingRequestCard(
                self._incoming_list,
                title=request["title"],
                details=request["details"],
                on_accept=lambda req=request: self._on_incoming_action(req["id"], "accept"),
                on_decline=lambda req=request: self._on_incoming_action(req["id"], "decline"),
            )
            card.grid(row=index, column=0, padx=14, pady=(14 if index == 0 else 0, 14), sticky="ew")

    def set_selected_device(self, device_name: str | None) -> None:
        self._selected_device_name = device_name
        for name, card in self._device_cards.items():
            card.set_selected(name == device_name)

    def _handle_device_select(self, device_name: str) -> None:
        self.set_selected_device(device_name)
        self._on_device_selected(device_name)

    def _open_manual_connect(self) -> None:
        dialog = ManualConnectDialog(self.winfo_toplevel(), on_connect=self._submit_manual_connect)
        dialog.focus()

    def _submit_manual_connect(self, ip: str, port: int) -> None:
        if self._on_manual_connect:
            self._on_manual_connect(ip, port)


class ManualConnectDialog(ctk.CTkToplevel):
    def __init__(self, master, *, on_connect) -> None:
        super().__init__(master)
        self.title("Connect by IP")
        self.geometry("380x200")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.configure(fg_color="#10151F")
        self._on_connect = on_connect
        self.after(10, self._center)

        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)

        header = ctk.CTkLabel(self, text="Manual Connect", font=ctk.CTkFont(size=20, weight="bold"))
        header.grid(row=0, column=0, columnspan=2, padx=24, pady=(20, 6), sticky="w")

        hint = ctk.CTkLabel(
            self,
            text="Enter iPhone IP (Settings > Wi-Fi > tap your network)",
            text_color="#98A4B8",
            font=ctk.CTkFont(size=12),
        )
        hint.grid(row=1, column=0, columnspan=2, padx=24, pady=(0, 14), sticky="w")

        self._ip_entry = ctk.CTkEntry(self, placeholder_text="e.g. 192.168.1.74")
        self._ip_entry.grid(row=2, column=0, padx=(24, 10), pady=(0, 18), sticky="ew")

        self._port_entry = ctk.CTkEntry(self, placeholder_text="8771", width=80)
        self._port_entry.insert(0, "8771")
        self._port_entry.grid(row=2, column=1, padx=(0, 24), pady=(0, 18))

        connect_btn = ctk.CTkButton(
            self,
            text="Connect",
            command=self._submit,
            fg_color="#D98F2B",
            hover_color="#C8801B",
            text_color="#111111",
            height=38,
            corner_radius=14,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        connect_btn.grid(row=3, column=0, columnspan=2, padx=24, pady=(0, 20), sticky="ew")

    def _submit(self) -> None:
        ip = self._ip_entry.get().strip()
        if not ip:
            return
        try:
            port = int(self._port_entry.get().strip() or "8771")
        except ValueError:
            port = 8771
        self._on_connect(ip, port)
        self.destroy()

    def _center(self) -> None:
        self.update_idletasks()
        px = self.master.winfo_rootx()
        py = self.master.winfo_rooty()
        pw = self.master.winfo_width()
        ph = self.master.winfo_height()
        w = self.winfo_width()
        h = self.winfo_height()
        self.geometry(f"+{px + max((pw - w) // 2, 0)}+{py + max((ph - h) // 2, 0)}")
