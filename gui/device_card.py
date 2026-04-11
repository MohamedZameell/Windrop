from __future__ import annotations

import customtkinter as ctk


class DeviceCard(ctk.CTkFrame):
    """Selectable card that represents a discovered nearby device."""

    def __init__(
        self,
        master,
        *,
        name: str,
        subtitle: str,
        status: str,
        on_select,
        selected: bool = False,
    ) -> None:
        super().__init__(
            master,
            fg_color="#141A24",
            corner_radius=16,
            border_width=1,
            border_color="#263041",
        )
        self._name = name
        self._on_select = on_select

        self.grid_columnconfigure(0, weight=1)

        badge = ctk.CTkLabel(
            self,
            text="iPhone",
            width=58,
            height=26,
            corner_radius=13,
            fg_color="#1D6B57",
            text_color="#E9FFF6",
            font=ctk.CTkFont(size=11, weight="bold"),
        )
        badge.grid(row=0, column=0, padx=16, pady=(14, 8), sticky="w")

        name_label = ctk.CTkLabel(
            self,
            text=name,
            anchor="w",
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        name_label.grid(row=1, column=0, padx=16, sticky="ew")

        subtitle_label = ctk.CTkLabel(
            self,
            text=subtitle,
            anchor="w",
            text_color="#9CA7B8",
            font=ctk.CTkFont(size=12),
        )
        subtitle_label.grid(row=2, column=0, padx=16, pady=(4, 0), sticky="ew")

        status_label = ctk.CTkLabel(
            self,
            text=status,
            anchor="w",
            text_color="#6ED9B3",
            font=ctk.CTkFont(size=12, weight="bold"),
        )
        status_label.grid(row=3, column=0, padx=16, pady=(8, 14), sticky="ew")

        for widget in (self, badge, name_label, subtitle_label, status_label):
            widget.bind("<Button-1>", self._handle_select)

        self.set_selected(selected)

    @property
    def name(self) -> str:
        return self._name

    def set_selected(self, selected: bool) -> None:
        self.configure(
            border_color="#D98F2B" if selected else "#263041",
            fg_color="#182132" if selected else "#141A24",
        )

    def _handle_select(self, _event) -> None:
        self._on_select(self._name)
