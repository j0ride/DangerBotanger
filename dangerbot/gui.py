"""Local desktop setup and bot controls."""
import asyncio
import logging
from pathlib import Path
from queue import Empty, Queue
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from urllib.parse import urlparse
import webbrowser

from .desktop_worker import TaskRunner
from .settings import FIELDS, apply_settings, ensure_certificate, load_settings, save_settings, validate_settings


class GuiLogHandler(logging.Handler):
    def __init__(self, events, secrets):
        super().__init__(logging.INFO)
        self.events, self.secrets = events, secrets
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))

    def emit(self, record):
        message = self.format(record)
        for secret in self.secrets():
            if secret:
                message = message.replace(secret, "[oculto]")
        self.events.put(("log", message))


class Desktop:
    def __init__(self, root, directory=None):
        self.root = root
        self.directory = Path(directory or Path.cwd()).resolve()
        self.env_path = self.directory / ".env"
        self.events = Queue()
        self.runner = TaskRunner(self.events)
        self.operation = None
        self.closing = False
        self.dirty = False
        self.controls = []
        self.action_buttons = []
        self.secret_entries = []
        self.variables = {}
        self.status = tk.StringVar(value="Bot parado — configure as contas para começar.")
        self.auth_status = tk.StringVar()
        self.show_secrets = tk.BooleanVar(value=False)
        root.title("DangerBotanger — Configuração e controle")
        root.geometry("1000x820")
        root.minsize(850, 680)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TButton", padding=(12, 7))
        style.configure("Title.TLabel", font=("Segoe UI", 19, "bold"))
        style.configure("TNotebook.Tab", padding=(15, 7))
        outer = ttk.Frame(root, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="DangerBotanger", style="Title.TLabel").pack(anchor="w")
        ttk.Label(outer, text="Configure suas contas, autorize no navegador e inicie o bot.").pack(anchor="w", pady=(3, 12))
        ttk.Label(outer, textvariable=self.status).pack(anchor="w", pady=(0, 4))
        ttk.Label(outer, textvariable=self.auth_status).pack(anchor="w", pady=(0, 12))
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        settings = load_settings(self.env_path)
        explanations = {
            "Twitch": "Use os logins sem URL. Cadastre um aplicativo no painel da Twitch e copie as credenciais. Autorize usando a conta do bot. A URL de callback precisa ser a mesma cadastrada no aplicativo.",
            "Spotify": "Cadastre um aplicativo no painel do Spotify e copie as credenciais. Autorize a conta que reproduzirá as músicas (Premium). Sem ID de dispositivo, o bot usa o dispositivo ativo. Cadastre a URL de callback abaixo no aplicativo.",
            "Pedidos": "everyone = todos; subscriber = inscritos; moderator = moderadores; broadcaster = dono do canal. true = sim; false = não. Nas listas de bloqueio, separe os valores por vírgulas.",
            "Avançado": "Os caminhos podem ser relativos à pasta do projeto. A autenticação HTTPS da Twitch precisa de um certificado local; ele é criado automaticamente ao autorizar. Nenhum certificado é instalado no Windows.",
        }
        for group, description in explanations.items():
            tab = ttk.Frame(notebook)
            notebook.add(tab, text=group)
            canvas = tk.Canvas(tab, highlightthickness=0)
            scrollbar = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
            canvas.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            content = ttk.Frame(canvas, padding=15)
            window = canvas.create_window((0, 0), window=content, anchor="nw")
            canvas.bind("<Configure>", lambda event, c=canvas, w=window: c.itemconfigure(w, width=event.width))
            content.bind("<Configure>", lambda event, c=canvas: c.configure(scrollregion=c.bbox("all")))
            content.columnconfigure(1, weight=1)
            ttk.Label(content, text=description, wraplength=730, justify="left").grid(
                row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
            row = 1
            for field in FIELDS:
                if field.group != group:
                    continue
                variable = tk.StringVar(value=settings[field.key])
                variable.trace_add("write", self._changed)
                self.variables[field.key] = variable
                ttk.Label(content, text=field.label).grid(row=row, column=0, sticky="w", padx=(0, 15), pady=5)
                if field.choices:
                    control = ttk.Combobox(content, textvariable=variable, values=field.choices, state="readonly")
                else:
                    control = ttk.Entry(content, textvariable=variable, show="•" if field.secret else "")
                control.grid(row=row, column=1, sticky="ew", pady=5)
                self.controls.append(control)
                if field.secret:
                    self.secret_entries.append(control)
                row += 1
            if group in {"Spotify", "Twitch"}:
                actions = ttk.Frame(content)
                actions.grid(row=row, column=0, columnspan=2, sticky="w", pady=(14, 0))
                provider = group.lower()
                self._button(actions, "Autorizar " + group, lambda p=provider: self.authenticate(p)).pack(side="left")
                dashboard = ("https://developer.spotify.com/dashboard" if provider == "spotify"
                             else "https://dev.twitch.tv/console/apps")
                self._button(actions, "Abrir painel de aplicativos", lambda url=dashboard: webbrowser.open(url)).pack(side="left", padx=8)
            elif group == "Avançado":
                self._button(content, "Preparar certificado local", self.prepare_certificate).grid(
                    row=row, column=0, columnspan=2, sticky="w", pady=(14, 0))
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=12)
        self._button(actions, "Salvar configurações", self.save).pack(side="left")
        self._button(actions, "Iniciar bot", self.start_bot).pack(side="left", padx=8)
        self.stop_button = ttk.Button(actions, text="Parar / cancelar", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")
        reveal = ttk.Checkbutton(actions, text="Mostrar secrets", variable=self.show_secrets, command=self._toggle_secrets)
        reveal.pack(side="right")
        self.controls.append(reveal)
        ttk.Label(outer, text="Atividade").pack(anchor="w")
        self.output = scrolledtext.ScrolledText(outer, height=8, state="disabled", wrap="word",
                                              font=("Consolas", 10))
        self.output.pack(fill="x", pady=(4, 0))
        self.log_handler = GuiLogHandler(self.events, lambda: [
            self.saved_values.get(field.key, "") for field in FIELDS if field.secret])
        self.saved_values = settings
        logging.getLogger().addHandler(self.log_handler)
        logging.getLogger().setLevel(logging.INFO)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._refresh_auth_status()
        self._append("Configurações carregadas. Altere as abas e clique em Salvar configurações.")
        root.after(100, self._poll)

    def _button(self, parent, text, command):
        button = ttk.Button(parent, text=text, command=command)
        self.action_buttons.append(button)
        return button

    def _changed(self, *_):
        self.dirty = True

    def _toggle_secrets(self):
        for entry in self.secret_entries:
            entry.configure(show="" if self.show_secrets.get() else "•")

    def _refresh_auth_status(self):
        states = []
        for provider in ("spotify", "twitch"):
            exists = (self.directory / "data" / (provider + "-tokens.json")).exists()
            states.append(f"{provider.title()}: " + ("autorização salva" if exists else "ainda não autorizado"))
        self.auth_status.set("  |  ".join(states))

    def _append(self, message):
        for field in FIELDS:
            if field.secret and self.saved_values.get(field.key):
                message = message.replace(self.saved_values[field.key], "[oculto]")
        self.output.configure(state="normal")
        self.output.insert("end", message + "\n")
        if int(self.output.index("end-1c").split(".")[0]) > 500:
            self.output.delete("1.0", "100.0")
        self.output.see("end")
        self.output.configure(state="disabled")

    def save(self, *, provider=None, require_accounts=False):
        if self.runner.active:
            return False
        try:
            values = validate_settings({key: variable.get() for key, variable in self.variables.items()},
                                       provider=provider, require_accounts=require_accounts)
            self.saved_values = save_settings(self.env_path, values)
            apply_settings(self.saved_values)
        except (OSError, ValueError) as error:
            messagebox.showerror("Confira as configurações", str(error), parent=self.root)
            return False
        self.dirty = False
        self._append("Configurações salvas no .env local.")
        self.status.set("Configurações salvas. Autorize as contas ou inicie o bot.")
        return True

    def _certificate_paths(self):
        return tuple(self.directory / self.saved_values[key] for key in ("OAUTH_TLS_CERT", "OAUTH_TLS_KEY"))

    def _set_busy(self, busy):
        for control in self.controls:
            control.configure(state="disabled" if busy else ("readonly" if isinstance(control, ttk.Combobox) else "normal"))
        for button in self.action_buttons:
            button.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")

    def _start(self, name, factory):
        self.operation = name
        self._set_busy(True)
        self.status.set(name + "…")
        self._append(name + ".")
        self.runner.start(factory)

    def authenticate(self, provider):
        if not self.save(provider=provider):
            return
        async def action():
            from .oauth import authorize
            if urlparse(self.saved_values[provider.upper() + "_REDIRECT_URI"]).scheme == "https":
                await asyncio.to_thread(ensure_certificate, *self._certificate_paths())
                self.events.put(("log", "No aviso de certificado do navegador, prossiga apenas para o callback local configurado."))
            await authorize(provider, announce=lambda message: self.events.put(("log", message)), show_url=False)
        self._start("Autorizando " + provider.title(), action)

    def prepare_certificate(self):
        if not self.save():
            return
        async def action():
            created = await asyncio.to_thread(ensure_certificate, *self._certificate_paths())
            self.events.put(("log", "Certificado local criado." if created else "Certificado local existente validado."))
        self._start("Preparando certificado", action)

    def start_bot(self):
        if not self.save(require_accounts=True):
            return
        missing = [provider.title() for provider in ("spotify", "twitch")
                   if not (self.directory / "data" / (provider + "-tokens.json")).exists()]
        if missing:
            messagebox.showinfo("Autorize as contas", "Use o botão Autorizar nas abas: " + ", ".join(missing), parent=self.root)
            return
        from .app import run
        self._start("Bot em execução", run)

    def stop(self):
        self.runner.stop()
        self.status.set("Encerrando a operação…")
        self.stop_button.configure(state="disabled")

    def _poll(self):
        finished = None
        for _ in range(200):
            try:
                kind, message = self.events.get_nowait()
            except Empty:
                break
            if kind == "finished":
                finished = message
            else:
                self._append(message)
                if kind == "error" and not self.closing:
                    messagebox.showerror("Operação não concluída", message, parent=self.root)
        if finished is not None:
            self._set_busy(False)
            self._refresh_auth_status()
            message = {"success": "Operação concluída.", "cancelled": "Operação encerrada.",
                       "error": "Operação não concluída; confira a atividade acima."}[finished]
            self.status.set(message)
            self._append(message)
            self.operation = None
        if self.closing and not self.runner.active:
            self.dispose()
            return
        self.root.after(100, self._poll)

    def close(self):
        if self.closing:
            return
        if self.dirty and not self.runner.active:
            answer = messagebox.askyesnocancel("Configurações não salvas", "Salvar as alterações antes de fechar?", parent=self.root)
            if answer is None or (answer and not self.save()):
                return
        self.closing = True
        if self.runner.active:
            self.stop()
        else:
            self.dispose()

    def dispose(self):
        logging.getLogger().removeHandler(self.log_handler)
        self.root.destroy()


def main():
    root = tk.Tk()
    Desktop(root)
    root.mainloop()
