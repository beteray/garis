// GARIS Desktop — the shell around the engine.
//
// Deliberately thin. It owns four things the web layer cannot: the tray, the
// window's native glass, autostart, and the lifetime of the engine process.
// Everything else goes over the local API, which is why the same UI can later
// point at a server agent or run in a browser during development.
//
// The rule that shapes this file: closing the window must never stop the work.
// The window hides; the engine keeps running; the tray is how you come back.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::OpenOptions;
use std::io::{BufRead, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::Serialize;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, State, WindowEvent};

#[derive(Default, Serialize, Clone)]
struct EngineInfo {
    base: String,
    token: String,
    /// Why there is no engine, in a sentence the window can show.
    ///
    /// A release build sets `windows_subsystem = "windows"`, so it has no console
    /// to print to: without this the user gets a window that never connects and
    /// no way to find out why.
    error: String,
}

#[derive(Default)]
struct Engine {
    info: Mutex<EngineInfo>,
    child: Mutex<Option<Child>>,
}

/// Where the engine is and how to authenticate to it.
///
/// The UI asks for this instead of storing a token: the shell started the engine,
/// so the shell is the only thing that legitimately knows the secret.
#[tauri::command]
fn engine_info(engine: State<'_, Engine>) -> EngineInfo {
    engine.info.lock().expect("engine info poisoned").clone()
}

#[tauri::command]
fn open_window(app: AppHandle) {
    show(&app);
}

/// Where the engine's own output goes.
///
/// A packaged build has no console, so without a file on disk a failed start is
/// only diagnosable by someone who happens to be watching — which is nobody, at
/// login.
fn log_path(app: &AppHandle) -> Option<PathBuf> {
    let directory = app.path().app_log_dir().ok()?;
    std::fs::create_dir_all(&directory).ok()?;
    Some(directory.join("engine.log"))
}

fn append_line(path: &Option<PathBuf>, line: &str) {
    let Some(path) = path else { return };
    if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(file, "{line}");
    }
}

/// Stop the engine *we* started.
///
/// Idempotent, and deliberately does nothing when we merely attached to an
/// engine someone else was running — killing that one would stop work this app
/// never owned.
fn stop_engine(app: &AppHandle) {
    let engine = app.state::<Engine>();
    let mut slot = engine.child.lock().expect("engine child poisoned");
    if let Some(mut child) = slot.take() {
        let _ = child.kill();
        // Reap it. Without the wait the process lingers, and on Windows the
        // installer cannot replace a file a dead-but-unreaped process still holds.
        let _ = child.wait();
    }
}

/// Tie the engine's lifetime to ours at the level of the operating system.
///
/// `stop_engine` on exit covers leaving through the tray, but nothing running
/// inside this process can clean up after a Task Manager kill, a crash, or a
/// session ending abruptly — the handler simply never runs, and the engine is
/// left serving on port 8756 with no window attached to it.
///
/// A job object moves the guarantee into the kernel: when the last handle to the
/// job closes — which is what happens when this process dies, however it dies —
/// Windows terminates everything in the job.
#[cfg(windows)]
fn tie_to_our_lifetime(child: &Child) {
    use std::ffi::c_void;
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Foundation::HANDLE;
    use windows_sys::Win32::System::JobObjects::{
        JobObjectExtendedLimitInformation, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    // windows-sys 0.59 ships the job-object types but not the three calls that
    // use them, so they are declared here rather than pulling in a second, far
    // larger Windows crate for the sake of one feature.
    #[link(name = "kernel32")]
    extern "system" {
        fn CreateJobObjectW(attributes: *const c_void, name: *const u16) -> HANDLE;
        fn SetInformationJobObject(
            job: HANDLE,
            class: i32,
            information: *const c_void,
            length: u32,
        ) -> i32;
        fn AssignProcessToJobObject(job: HANDLE, process: HANDLE) -> i32;
    }

    // SAFETY: plain Win32 calls on handles we own. Every failure path is a
    // no-op — losing the guarantee is survivable, panicking on startup is not.
    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return;
        }

        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            std::ptr::addr_of!(limits).cast(),
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );

        AssignProcessToJobObject(job, child.as_raw_handle() as _);
        // The handle is deliberately never closed: it has to outlive this
        // function, and closing it is exactly what kills the engine.
    }
}

#[cfg(not(windows))]
fn tie_to_our_lifetime(_child: &Child) {}

/// Start `garis serve` and read back the address and token it prints.
///
/// If an engine is already listening (started by hand, by the installer, or by a
/// previous run that outlived this window), we attach to it rather than starting
/// a second one — two engines on one state directory would fight over the same
/// database.
fn start_engine(app: &AppHandle) {
    let engine = app.state::<Engine>();

    if let Some(existing) = attach_to_running() {
        *engine.info.lock().expect("engine info poisoned") = existing;
        return;
    }

    let log = log_path(app);
    append_line(&log, &format!("--- start: {} ---", engine_binary()));

    let mut command = Command::new(engine_binary());
    command
        .args(["serve", "--print-token", "--port", "8756"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // CREATE_NO_WINDOW: an agent that runs at login must not flash a console.
        command.creation_flags(0x0800_0000);
    }

    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            let message = format!(
                "Nie udało się uruchomić silnika GARIS ({}). Sprawdź, czy plik `{}` istnieje.",
                error,
                engine_binary()
            );
            append_line(&log, &message);
            engine.info.lock().expect("engine info poisoned").error = message;
            return;
        }
    };

    tie_to_our_lifetime(&child);

    if let Some(stdout) = child.stdout.take() {
        let handle = app.clone();
        let log = log.clone();
        std::thread::spawn(move || {
            let mut base = String::new();
            let mut token = String::new();
            let mut settled = false;

            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                // The token is a credential. It goes to the shell's memory and
                // nowhere else — least of all a plaintext log.
                if let Some(rest) = line.strip_prefix("Token: ") {
                    token = rest.trim().to_string();
                    append_line(&log, "Token: (pominięty w logu)");
                } else {
                    append_line(&log, &line);
                    if let Some(rest) = line.strip_prefix("API: ") {
                        base = rest.trim().to_string();
                    }
                }

                // Keep draining afterwards rather than breaking: dropping the
                // reader closes the pipe, and the engine's next write to stdout
                // would then fail underneath it.
                if !settled && !base.is_empty() && !token.is_empty() {
                    *handle
                        .state::<Engine>()
                        .info
                        .lock()
                        .expect("engine info poisoned") = EngineInfo {
                        base: base.clone(),
                        token: token.clone(),
                        error: String::new(),
                    };
                    settled = true;
                }
            }
        });
    }

    if let Some(stderr) = child.stderr.take() {
        let log = log.clone();
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                append_line(&log, &line);
            }
        });
    }

    *engine.child.lock().expect("engine child poisoned") = Some(child);
}

fn attach_to_running() -> Option<EngineInfo> {
    let output = Command::new(engine_binary()).arg("token").output().ok()?;
    if !output.status.success() {
        return None;
    }
    let token = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if token.is_empty() {
        return None;
    }
    let probe = std::net::TcpStream::connect_timeout(
        &"127.0.0.1:8756".parse().ok()?,
        std::time::Duration::from_millis(400),
    );
    probe.ok()?;
    Some(EngineInfo {
        base: "http://127.0.0.1:8756".into(),
        token,
        error: String::new(),
    })
}

fn engine_binary() -> String {
    if let Ok(explicit) = std::env::var("GARIS_ENGINE") {
        return explicit;
    }

    // A packaged build ships the engine next to the executable, so look there
    // before falling back to PATH. An installed GARIS must not depend on what
    // happens to be on the user's PATH — and on a developer machine it must not
    // silently pick up a half-finished checkout either.
    if let Ok(exe) = std::env::current_exe() {
        if let Some(directory) = exe.parent() {
            let beside = directory.join(if cfg!(windows) { "garis.exe" } else { "garis" });
            if beside.is_file() {
                return beside.to_string_lossy().into_owned();
            }
        }
    }

    "garis".into()
}

fn show(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn main() {
    tauri::Builder::default()
        .manage(Engine::default())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec!["--minimised"]),
        ))
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .invoke_handler(tauri::generate_handler![engine_info, open_window])
        .setup(|app| {
            let handle = app.handle().clone();
            start_engine(&handle);

            // --- tray: the app's real home ---------------------------------
            let open = MenuItem::with_id(app, "open", "Otwórz GARIS-a", true, None::<&str>)?;
            let tasks = MenuItem::with_id(app, "tasks", "Aktywne zadania", true, None::<&str>)?;
            let separator = PredefinedMenuItem::separator(app)?;
            let quit = MenuItem::with_id(app, "quit", "Zakończ", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &tasks, &separator, &quit])?;

            TrayIconBuilder::with_id("garis")
                .icon(app.default_window_icon().expect("brak ikony okna").clone())
                .tooltip("GARIS")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "open" | "tasks" => show(app),
                    "quit" => {
                        // The only deliberate path out. Everything else — closing
                        // the window, logging out of the UI — leaves work running.
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show(tray.app_handle());
                    }
                })
                .build(app)?;

            // --- window: native glass --------------------------------------
            if let Some(window) = app.get_webview_window("main") {
                #[cfg(target_os = "windows")]
                {
                    use window_vibrancy::apply_mica;
                    // Mica gives the blur real desktop to work with. Failure is
                    // not fatal: the CSS gradient fallback covers older builds.
                    let _ = apply_mica(&window, None);
                }
                #[cfg(target_os = "macos")]
                {
                    use window_vibrancy::{apply_vibrancy, NSVisualEffectMaterial};
                    let _ = apply_vibrancy(&window, NSVisualEffectMaterial::HudWindow, None, None);
                }

                // Autostart hands us --minimised so login does not throw a window
                // in the user's face.
                if std::env::args().any(|arg| arg == "--minimised") {
                    let _ = window.hide();
                }
            }

            // --- global shortcut -------------------------------------------
            #[cfg(desktop)]
            {
                use tauri_plugin_global_shortcut::{
                    Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState,
                };

                let summon = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::KeyG);
                let handle_for_shortcut = handle.clone();
                app.handle().plugin(
                    tauri_plugin_global_shortcut::Builder::new()
                        .with_handler(move |_app, shortcut, event| {
                            if shortcut == &summon && event.state() == ShortcutState::Pressed {
                                show(&handle_for_shortcut);
                            }
                        })
                        .build(),
                )?;
                let _ = app.global_shortcut().register(summon);
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                // The product rule, in three lines: hide, do not exit.
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("nie udało się zbudować aplikacji GARIS")
        .run(|app, event| match event {
            RunEvent::ExitRequested { api, .. } => {
                // Closing the last window is not a reason to quit; only the tray's
                // "Zakończ" is.
                api.prevent_exit();
            }
            // Whichever way we actually left — the tray, a signal, the session
            // ending — the engine must not outlive the shell that started it.
            // Hanging this off the exit event rather than the tray handler is
            // what stops an orphan surviving every other route out.
            RunEvent::Exit => stop_engine(app),
            _ => {}
        });
}
