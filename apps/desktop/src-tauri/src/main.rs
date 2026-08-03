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
use std::sync::{Condvar, Mutex};
use std::time::Duration;

use serde::Serialize;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, RunEvent, State, WindowEvent};

/// How far the engine has got. The window needs this to tell "still coming up"
/// apart from "did not come up", which are the same picture without it.
#[derive(Default, Serialize, Clone, PartialEq, Debug)]
enum Phase {
    #[default]
    Starting,
    Ready,
    Failed,
}

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
    phase: Phase,
    /// The sidecar's exit code, when it exited. `None` while it is running.
    exit_code: Option<i32>,
    /// Where the log is, so the window can offer to open it. Never a secret.
    log: String,
}

#[derive(Default)]
struct Engine {
    info: Mutex<EngineInfo>,
    child: Mutex<Option<Child>>,
    /// Signalled when the engine reaches a settled phase, so `engine_info` can
    /// wait for an answer instead of returning an empty one.
    settled: Condvar,
}

/// How long the window waits for the engine before giving up on it.
///
/// A Python sidecar unpacked by PyInstaller on a cold disk, opening a SQLite
/// database and checking providers, is not instant. Twenty seconds is generous
/// enough to cover a slow first run and short enough that a genuinely dead
/// engine is reported rather than waited on forever.
const READY_TIMEOUT: Duration = Duration::from_secs(20);

/// Where the engine is and how to authenticate to it.
///
/// The UI asks for this instead of storing a token: the shell started the engine,
/// so the shell is the only thing that legitimately knows the secret.
///
/// **This waits.** `start_engine` returns as soon as the process is spawned,
/// but the address and token only arrive a second or two later, on the pipe.
/// Returning the empty info in the meantime is what made every launch look like
/// a failed one: the window asked once, at mount, got no token, and concluded
/// the engine was dead while it was still starting.
#[tauri::command]
fn engine_info(engine: State<'_, Engine>) -> EngineInfo {
    let info = engine.info.lock().expect("engine info poisoned");
    let (info, _timeout) = engine
        .settled
        .wait_timeout_while(info, READY_TIMEOUT, |info| info.phase == Phase::Starting)
        .expect("engine info poisoned");

    // A timeout is itself an answer, and a different one from "it crashed".
    if info.phase == Phase::Starting {
        let mut timed_out = info.clone();
        timed_out.phase = Phase::Failed;
        timed_out.error = format!(
            "Silnik nie zgłosił gotowości w {} s. Zobacz log.",
            READY_TIMEOUT.as_secs()
        );
        return timed_out;
    }
    info.clone()
}

#[tauri::command]
fn open_window(app: AppHandle) {
    show(&app);
}

/// Try the engine again, from the window, without restarting the whole app.
///
/// The alternative is telling someone whose agent did not come up to quit and
/// reopen it — which is the sort of advice that makes an app feel broken even
/// when the retry would have worked.
#[tauri::command]
fn restart_engine(app: AppHandle) -> EngineInfo {
    stop_engine(&app);
    *app.state::<Engine>()
        .info
        .lock()
        .expect("engine info poisoned") = EngineInfo::default();
    start_engine(&app);
    // Through `engine_info`, so the retry waits for readiness exactly like the
    // first attempt does. Reading the info straight back was the same bug in a
    // second place: the button reported failure before the engine had a chance.
    engine_info(app.state::<Engine>())
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

/// The log's location as plain text, for the diagnostics the user can copy.
/// A path is not a secret, and "gdzie jest log" is the first question support
/// asks.
fn log_text(path: &Option<PathBuf>) -> String {
    path.as_ref()
        .map(|path| path.to_string_lossy().into_owned())
        .unwrap_or_default()
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
        // Port 0: the engine binds whatever is free and prints back the real
        // address, which we parse below. Asking for 8756 meant that a stale
        // sidecar — or anything else on that port — made the engine die on
        // "address already in use" with nobody to report it to.
        .args(["serve", "--print-token", "--port", "0"])
        // The engine speaks Polish. Without these, a Python started with no
        // console picks cp1250 on Windows and the handshake line dies on its
        // first "ł" — before the window ever learns the address or the token.
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8:replace")
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
            let mut info = engine.info.lock().expect("engine info poisoned");
            info.error = message;
            info.phase = Phase::Failed;
            info.log = log_text(&log);
            engine.settled.notify_all();
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

            read_lines_lossy(stdout, |line| {
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
                    let state = handle.state::<Engine>();
                    *state.info.lock().expect("engine info poisoned") = EngineInfo {
                        base: base.clone(),
                        token: token.clone(),
                        error: String::new(),
                        phase: Phase::Ready,
                        exit_code: None,
                        log: log_text(&log),
                    };
                    // Releases whoever is blocked in `engine_info`.
                    state.settled.notify_all();
                    settled = true;
                }
            });
        });
    }

    if let Some(stderr) = child.stderr.take() {
        let log = log.clone();
        std::thread::spawn(move || {
            read_lines_lossy(stderr, |line| append_line(&log, &line));
        });
    }

    // Supervision. Without this, a sidecar that starts and then dies — a taken
    // port, a missing DLL, a corrupt state directory — leaves the window
    // waiting on a handshake that will never come, with no exit code and no
    // reason to show. Polling with `try_wait` rather than blocking in `wait`,
    // because the child lives behind the same mutex `stop_engine` needs: a
    // supervisor holding it across a blocking wait would deadlock shutdown.
    let supervisor = app.clone();
    let supervised = log.clone();
    std::thread::spawn(move || loop {
        std::thread::sleep(Duration::from_millis(250));
        let state = supervisor.state::<Engine>();
        let exited = {
            let mut slot = state.child.lock().expect("engine child poisoned");
            match slot.as_mut() {
                // Taken away by `stop_engine`: that exit was asked for.
                None => return,
                Some(process) => match process.try_wait() {
                    Ok(Some(status)) => status.code(),
                    Ok(None) => continue,
                    Err(_) => None,
                },
            }
        };

        let mut info = state.info.lock().expect("engine info poisoned");
        info.exit_code = exited;
        if info.phase != Phase::Ready {
            info.phase = Phase::Failed;
            info.log = log_text(&supervised);
            if info.error.is_empty() {
                info.error = match exited {
                    Some(code) => {
                        format!("Silnik zakończył się z kodem {code}, zanim zdążył odpowiedzieć.")
                    }
                    None => "Silnik zniknął, zanim zdążył odpowiedzieć.".into(),
                };
            }
            append_line(&supervised, &info.error);
        } else {
            // It was working and then stopped. The window finds out through
            // the socket dropping, but the exit code belongs in diagnostics.
            append_line(
                &supervised,
                &format!("--- silnik zakończył się (kod {exited:?}) ---"),
            );
        }
        state.settled.notify_all();
        return;
    });

    *engine.child.lock().expect("engine child poisoned") = Some(child);
}

/// Read a pipe line by line, never stopping on a byte we cannot decode.
///
/// `BufRead::lines()` yields `Err` for a line that is not valid UTF-8, and the
/// `map_while(Result::ok)` it invites ends the loop there. One mis-encoded
/// character from a Python that ignored our environment would silently deafen
/// the shell for the rest of the session — including the handshake. Decoding
/// lossily costs a replacement glyph in the log instead.
fn read_lines_lossy(pipe: impl std::io::Read, mut on_line: impl FnMut(String)) {
    let mut reader = BufReader::new(pipe);
    let mut raw = Vec::new();
    loop {
        raw.clear();
        match reader.read_until(b'\n', &mut raw) {
            Ok(0) | Err(_) => return,
            Ok(_) => {}
        }
        while matches!(raw.last(), Some(b'\n') | Some(b'\r')) {
            raw.pop();
        }
        on_line(String::from_utf8_lossy(&raw).into_owned());
    }
}

fn attach_to_running() -> Option<EngineInfo> {
    // Where a running engine says it is. Written by `garis serve` and removed
    // on the way out — see `Paths.runtime_file`. Probing a fixed port instead
    // is what tied the shell to 8756: when anything else held that port the
    // engine died on bind, and when a *stale* file pointed at a dead engine
    // the shell attached to nothing. Hence both checks below.
    let home = std::env::var("GARIS_HOME")
        .ok()
        .map(PathBuf::from)
        .or_else(|| dirs_home().map(|home| home.join(".local").join("share").join("garis")))?;
    let raw = std::fs::read_to_string(home.join("runtime.json")).ok()?;
    let base = raw.split("\"url\"").nth(1)?.split('"').nth(1)?.to_string();
    if !base.starts_with("http://127.0.0.1:") {
        return None; // never attach to anything off the loopback
    }

    // The address existing is not the same as an engine answering it.
    let authority = base.trim_start_matches("http://");
    let probe = std::net::TcpStream::connect_timeout(
        &authority.parse().ok()?,
        std::time::Duration::from_millis(400),
    );
    probe.ok()?;

    let output = Command::new(engine_binary()).arg("token").output().ok()?;
    if !output.status.success() {
        return None;
    }
    let token = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if token.is_empty() {
        return None;
    }
    Some(EngineInfo {
        base,
        token,
        error: String::new(),
        phase: Phase::Ready,
        exit_code: None,
        log: String::new(),
    })
}

/// Pull the engine's address out of `runtime.json`, refusing anything that is
/// not loopback.
///
/// Hand-parsed rather than pulling in a JSON crate for one field — and the
/// loopback check is the security half: this file is written by the engine, but
/// a shell that trusted whatever address it found would happily send the local
/// token to any host somebody wrote into it.
fn loopback_url(raw: &str) -> Option<String> {
    let base = raw.split("\"url\"").nth(1)?.split('"').nth(1)?.to_string();
    let authority = base.strip_prefix("http://")?;
    let (host, port) = authority.rsplit_once(':')?;
    if host != "127.0.0.1" || port.parse::<u16>().is_err() {
        return None;
    }
    Some(base)
}

/// The user's home directory, without pulling in a crate for one lookup.
fn dirs_home() -> Option<PathBuf> {
    std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .ok()
        .map(PathBuf::from)
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
        .invoke_handler(tauri::generate_handler![
            engine_info,
            open_window,
            restart_engine
        ])
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_the_address_a_running_engine_published() {
        let raw = r#"{"url": "http://127.0.0.1:54321", "pid": 4242, "protocol": 1}"#;
        assert_eq!(loopback_url(raw).as_deref(), Some("http://127.0.0.1:54321"));
    }

    #[test]
    fn accepts_any_port_because_the_engine_picks_one() {
        // The whole point of the fix: 8756 is no longer special.
        for port in ["8756", "1", "65535"] {
            let raw = format!(r#"{{"url": "http://127.0.0.1:{port}"}}"#);
            assert!(loopback_url(&raw).is_some(), "port {port}");
        }
    }

    #[test]
    fn refuses_to_send_the_token_anywhere_but_loopback() {
        // A runtime file is not a trusted document: it is a file on disk that
        // anything running as this user can write.
        for hostile in [
            r#"{"url": "http://10.0.0.5:8756"}"#,
            r#"{"url": "http://evil.example.com:8756"}"#,
            r#"{"url": "https://127.0.0.1:8756"}"#,
            r#"{"url": "http://127.0.0.1.evil.com:8756"}"#,
        ] {
            assert_eq!(loopback_url(hostile), None, "{hostile}");
        }
    }

    #[test]
    fn survives_a_runtime_file_that_is_rubbish() {
        for broken in [
            "",
            "{",
            "{}",
            r#"{"url": ""}"#,
            r#"{"url": "http://127.0.0.1:port"}"#,
        ] {
            assert_eq!(loopback_url(broken), None, "{broken:?}");
        }
    }

    #[test]
    fn a_starting_engine_is_not_a_failed_one() {
        // The defect this release fixes, as a type-level assertion: Starting
        // and Failed are different phases, and the window branches on them.
        assert_ne!(Phase::default(), Phase::Failed);
        assert_eq!(Phase::default(), Phase::Starting);
    }
}
