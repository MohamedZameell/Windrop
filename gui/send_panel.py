from __future__ import annotations

from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
from tkinterdnd2 import DND_FILES


class SendPanel(ctk.CTkFrame):
    """Left-side panel for drag-and-drop file sending."""

    def __init__(self, master, *, on_files_selected, on_send_clicked) -> None:
        super().__init__(master, fg_color="#10151F", corner_radius=20)
        self._on_files_selected = on_files_selected
        self._on_send_clicked = on_send_clicked
        self._selected_device_name: str | None = None
        self._sending = False
        self._dropped_files: list[str] = []
        self._progress_state: dict[str, int] = {}

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkLabel(
            self,
            text="Send To iPhone",
            anchor="w",
            font=ctk.CTkFont(size=26, weight="bold"),
        )
        header.grid(row=0, column=0, padx=24, pady=(24, 16), sticky="ew")

        self._drop_shell = ctk.CTkFrame(
            self,
            fg_color="#131C29",
            corner_radius=18,
            border_width=1,
            border_color="#2C3648",
        )
        self._drop_shell.grid(row=1, column=0, padx=24, pady=(0, 20), sticky="nsew")
        self._drop_shell.grid_columnconfigure(0, weight=1)
        self._drop_shell.grid_rowconfigure(0, weight=1)

        self._drop_canvas = ctk.CTkCanvas(
            self._drop_shell,
            bg="#131C29",
            highlightthickness=0,
            bd=0,
        )
        self._drop_canvas.grid(row=0, column=0, sticky="nsew", padx=18, pady=18)
        self._drop_canvas.bind("<Configure>", self._redraw_dropzone)
        self._drop_canvas.bind("<Configure>", self._resize_embedded_content, add="+")
        self._drop_canvas.drop_target_register(DND_FILES)
        self._drop_canvas.dnd_bind("<<Drop>>", self._handle_drop_event)

        self._drop_stack = ctk.CTkFrame(self._drop_canvas, fg_color="transparent")
        self._drop_stack.grid_columnconfigure(0, weight=1)
        self._placeholder = self._build_placeholder(self._drop_stack)
        self._placeholder.grid(row=0, column=0, sticky="nsew")
        self._file_view = self._build_file_view(self._drop_stack)
        self._file_view.grid(row=0, column=0, sticky="nsew")
        self._drop_window = self._drop_canvas.create_window((0, 0), window=self._drop_stack, anchor="nw")

        self._feedback_label = ctk.CTkLabel(
            self,
            text="Drop files or browse to begin",
            anchor="w",
            text_color="#98A4B8",
            font=ctk.CTkFont(size=14),
        )
        self._feedback_label.grid(row=2, column=0, padx=24, sticky="ew")

        self._progress_bar = ctk.CTkProgressBar(self, height=12, corner_radius=999)
        self._progress_bar.grid(row=3, column=0, padx=24, pady=(12, 10), sticky="ew")
        self._progress_bar.set(0)

        self._send_button = ctk.CTkButton(
            self,
            text="Select files and a device",
            command=self._trigger_send,
            state="disabled",
            height=46,
            corner_radius=14,
            fg_color="#3D4657",
            hover_color="#485267",
            text_color="#D0D7E4",
            font=ctk.CTkFont(size=15, weight="bold"),
        )
        self._send_button.grid(row=4, column=0, padx=24, pady=(0, 24), sticky="ew")

        self._render_file_state()

    @property
    def files(self) -> list[str]:
        return list(self._dropped_files)

    @property
    def is_ready_to_send(self) -> bool:
        return bool(self._dropped_files and self._selected_device_name and not self._sending)

    @property
    def send_button_enabled(self) -> bool:
        return str(self._send_button.cget("state")) == "normal"

    def set_selected_device(self, device_name: str | None) -> None:
        self._selected_device_name = device_name
        self._refresh_send_button()

    def add_files(self, paths: list[str]) -> None:
        existing = {Path(path).resolve() for path in self._dropped_files if Path(path).exists()}
        updated_files = list(self._dropped_files)

        for raw_path in paths:
            path = str(Path(raw_path).expanduser())
            path_obj = Path(path)
            if not path_obj.exists() or path_obj.is_dir():
                continue
            resolved = path_obj.resolve()
            if resolved in existing:
                continue
            existing.add(resolved)
            updated_files.append(str(resolved))

        self._set_files(updated_files)

    def remove_file(self, path: str) -> None:
        self._set_files([file_path for file_path in self._dropped_files if file_path != path])

    def start_send(self) -> None:
        self._sending = True
        self._progress_state = {Path(path).name: 0 for path in self._dropped_files}
        self._feedback_label.configure(text="Sending files...", text_color="#D98F2B")
        self._progress_bar.set(0)
        self._refresh_send_button()

    def update_progress(self, filename: str, bytes_sent: int, total_bytes: int) -> None:
        self._progress_state[filename] = bytes_sent
        total_all = sum(Path(path).stat().st_size for path in self._dropped_files if Path(path).exists())
        sent_all = sum(self._progress_state.values())
        ratio = (sent_all / total_all) if total_all else 0
        self._progress_bar.set(max(0, min(ratio, 1)))
        self._feedback_label.configure(
            text=f"Sending {filename} ({self._format_bytes(bytes_sent)} / {self._format_bytes(total_bytes)})",
            text_color="#D98F2B",
        )

    def finish_success(self) -> None:
        self._sending = False
        self._set_files([])
        self._progress_bar.set(1)
        self._feedback_label.configure(text="✓ Sent successfully", text_color="#6ED9B3")
        self._refresh_send_button()

    def finish_error(self, message: str) -> None:
        self._sending = False
        self._feedback_label.configure(text=f"✗ {message}", text_color="#F27A7A")
        self._refresh_send_button()

    def _build_placeholder(self, master) -> ctk.CTkFrame:
        frame = ctk.CTkFrame(master, fg_color="transparent")
        frame.grid_columnconfigure(0, weight=1)

        drop_title = ctk.CTkLabel(
            frame,
            text="Drop files here to send to iPhone",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        drop_title.grid(row=0, column=0, pady=(42, 8), padx=30)

        drop_hint = ctk.CTkLabel(
            frame,
            text="Drag files from Explorer or browse from disk. The send button activates after you pick a nearby device.",
            text_color="#98A4B8",
            wraplength=340,
            justify="center",
            font=ctk.CTkFont(size=13),
        )
        drop_hint.grid(row=1, column=0, pady=(0, 18), padx=30)

        browse_button = ctk.CTkButton(
            frame,
            text="Browse Files",
            command=self._browse_files,
            fg_color="#D98F2B",
            hover_color="#C8801B",
            text_color="#111111",
            height=40,
            corner_radius=14,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        browse_button.grid(row=2, column=0, pady=(0, 42))
        return frame

    def _build_file_view(self, master) -> ctk.CTkScrollableFrame:
        frame = ctk.CTkScrollableFrame(
            master,
            fg_color="transparent",
            corner_radius=0,
            border_width=0,
            label_text="",
        )
        frame.grid_columnconfigure(0, weight=1)
        return frame

    def _render_file_state(self) -> None:
        for child in self._file_view.winfo_children():
            child.destroy()

        has_files = bool(self._dropped_files)
        if has_files:
            self._placeholder.grid_remove()
            self._file_view.grid()

            for index, path in enumerate(self._dropped_files):
                row = ctk.CTkFrame(
                    self._file_view,
                    fg_color="#172130",
                    corner_radius=14,
                    border_width=1,
                    border_color="#2C3648",
                )
                row.grid(row=index, column=0, padx=8, pady=(0 if index else 8, 8), sticky="ew")
                row.grid_columnconfigure(0, weight=1)

                file_path = Path(path)
                size_text = self._format_bytes(file_path.stat().st_size) if file_path.exists() else "Unknown"

                title = ctk.CTkLabel(
                    row,
                    text=file_path.name,
                    anchor="w",
                    font=ctk.CTkFont(size=14, weight="bold"),
                )
                title.grid(row=0, column=0, padx=16, pady=(12, 2), sticky="ew")

                subtitle = ctk.CTkLabel(
                    row,
                    text=size_text,
                    anchor="w",
                    text_color="#98A4B8",
                    font=ctk.CTkFont(size=12),
                )
                subtitle.grid(row=1, column=0, padx=16, pady=(0, 12), sticky="ew")

                remove_button = ctk.CTkButton(
                    row,
                    text="✕",
                    command=lambda item=path: self.remove_file(item),
                    width=32,
                    height=32,
                    corner_radius=10,
                    fg_color="#242C3B",
                    hover_color="#2D3647",
                    font=ctk.CTkFont(size=14, weight="bold"),
                )
                remove_button.grid(row=0, column=1, rowspan=2, padx=14, pady=12)
        else:
            self._file_view.grid_remove()
            self._placeholder.grid()

        self._feedback_label.configure(
            text=f"{len(self._dropped_files)} file(s) staged" if has_files else "Drop files or browse to begin",
            text_color="#98A4B8" if not self._sending else "#D98F2B",
        )
        if not self._sending and not has_files:
            self._progress_bar.set(0)
        self._on_files_selected(list(self._dropped_files))
        self._refresh_send_button()
        self._drop_canvas.update_idletasks()
        self._resize_embedded_content()

    def _set_files(self, paths: list[str]) -> None:
        self._dropped_files = paths
        self._render_file_state()

    def _browse_files(self) -> None:
        paths = filedialog.askopenfilenames(title="Select files to send")
        if paths:
            self.add_files(list(paths))

    def _handle_drop_event(self, event) -> str:
        self.add_files(self._parse_drop_data(event.data))
        return "break"

    def _parse_drop_data(self, data: str) -> list[str]:
        try:
            parsed = list(self.tk.splitlist(data))
            if parsed:
                return parsed
        except Exception:
            pass

        cleaned = data.strip()
        if not cleaned:
            return []

        paths: list[str] = []
        token = ""
        in_braces = False
        for char in cleaned:
            if char == "{":
                in_braces = True
                token = ""
                continue
            if char == "}":
                in_braces = False
                if token:
                    paths.append(token)
                    token = ""
                continue
            if char == " " and not in_braces:
                if token:
                    paths.append(token)
                    token = ""
                continue
            token += char
        if token:
            paths.append(token)
        return paths

    def _trigger_send(self) -> None:
        if self.is_ready_to_send:
            self._on_send_clicked()

    def _refresh_send_button(self) -> None:
        can_send = bool(self._dropped_files and self._selected_device_name and not self._sending)
        if can_send:
            self._send_button.configure(
                state="normal",
                text=f"Send To {self._selected_device_name}",
                fg_color="#D98F2B",
                hover_color="#C8801B",
                text_color="#111111",
            )
            return

        if self._sending:
            text = "Sending..."
        elif self._dropped_files and not self._selected_device_name:
            text = "Select an iPhone to continue"
        elif self._selected_device_name and not self._dropped_files:
            text = f"Add files for {self._selected_device_name}"
        else:
            text = "Select files and a device"

        self._send_button.configure(
            state="disabled",
            text=text,
            fg_color="#3D4657",
            hover_color="#485267",
            text_color="#D0D7E4",
        )

    def _redraw_dropzone(self, event) -> None:
        self._drop_canvas.delete("border")
        width = max(event.width - 6, 0)
        height = max(event.height - 6, 0)
        self._drop_canvas.create_rectangle(
            3,
            3,
            width,
            height,
            outline="#52617B",
            width=2,
            dash=(8, 6),
            tags="border",
        )

    def _resize_embedded_content(self, _event=None) -> None:
        width = max(self._drop_canvas.winfo_width() - 8, 40)
        height = max(self._drop_canvas.winfo_height() - 8, 40)
        self._drop_canvas.coords(self._drop_window, 4, 4)
        self._drop_canvas.itemconfigure(self._drop_window, width=width, height=height)

    @staticmethod
    def _format_bytes(size: int) -> str:
        units = ["B", "KB", "MB", "GB", "TB"]
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024
        return f"{size} B"
