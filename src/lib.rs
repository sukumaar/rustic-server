use axum::{
    body::Bytes,
    http::{header, StatusCode},
    routing::{on, MethodFilter},
    Router,
};
use pyo3::{
    exceptions::{PyOSError, PyRuntimeError, PyValueError},
    prelude::*,
};
use std::{
    collections::HashSet,
    net::{IpAddr, SocketAddr},
    sync::mpsc,
    time::Duration,
};

// PyO3 extracts Python tuples directly into owned Rust values at startup.
type Route = (String, String, u16, Vec<u8>);

fn build_router(routes: Vec<Route>) -> Result<Router, String> {
    if routes.is_empty() {
        return Err("Declare at least one route".into());
    }
    let mut router = Router::new();
    let mut seen = HashSet::new();
    for (method, path, status, body) in routes {
        if !path.starts_with('/')
            || path
                .chars()
                .any(|c| "{}*?#".contains(c) || c.is_whitespace() || c.is_control())
        {
            return Err(format!("Invalid exact path: {path}"));
        }
        if !seen.insert((method.clone(), path.clone())) {
            return Err(format!("Duplicate route: {method} {path}"));
        }
        let method = match method.as_str() {
            "GET" => MethodFilter::GET,
            "POST" => MethodFilter::POST,
            "PUT" => MethodFilter::PUT,
            "PATCH" => MethodFilter::PATCH,
            "DELETE" => MethodFilter::DELETE,
            "OPTIONS" => MethodFilter::OPTIONS,
            _ => return Err(format!("Unsupported method: {method}")),
        };
        if !(200..=599).contains(&status) || [204, 205, 304].contains(&status) {
            return Err("Status must allow a JSON response body".into());
        }
        serde_json::from_slice::<serde_json::Value>(&body)
            .map_err(|e| format!("Invalid JSON response: {e}"))?;
        let status = StatusCode::from_u16(status).map_err(|e| e.to_string())?;
        let body = Bytes::from(body);
        router = router.route(
            &path,
            on(method, move || {
                let body = body.clone();
                async move { (status, [(header::CONTENT_TYPE, "application/json")], body) }
            }),
        );
    }
    Ok(router)
}

#[pyfunction]
fn serve(py: Python<'_>, routes: Vec<Route>, host: &str, port: u16) -> PyResult<()> {
    let address = SocketAddr::new(
        host.parse::<IpAddr>()
            .map_err(|_| PyValueError::new_err("host must be an IPv4 or IPv6 address"))?,
        port,
    );
    let router = build_router(routes).map_err(PyValueError::new_err)?;
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .map_err(PyOSError::new_err)?;
    let listener = runtime
        .block_on(tokio::net::TcpListener::bind(address))
        .map_err(PyOSError::new_err)?;
    eprintln!(
        "Listening on http://{}",
        listener.local_addr().map_err(PyOSError::new_err)?
    );
    let (shutdown_tx, shutdown_rx) = tokio::sync::oneshot::channel();
    let (done_tx, mut done_rx) = mpsc::channel();
    runtime.spawn(async move {
        let result = axum::serve(listener, router)
            .with_graceful_shutdown(async {
                let _ = shutdown_rx.await;
            })
            .await;
        let _ = done_tx.send(result);
    });

    // Rust workers serve requests independently. Release the GIL while waiting,
    // checking Python signals on the calling main thread every 100 ms.
    let result = loop {
        match py.detach(|| (&mut done_rx).recv_timeout(Duration::from_millis(100))) {
            Ok(result) => break result.map_err(PyOSError::new_err),
            Err(mpsc::RecvTimeoutError::Disconnected) => {
                break Err(PyRuntimeError::new_err("Rust server stopped unexpectedly"))
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                if let Err(error) = py.check_signals() {
                    break Err(error);
                }
            }
        }
    };
    let _ = shutdown_tx.send(());
    py.detach(move || {
        // Bound shutdown even if a client keeps a connection open.
        let _ = done_rx.recv_timeout(Duration::from_secs(5));
        runtime.shutdown_timeout(Duration::from_secs(1));
    });
    result
}

#[pymodule]
fn _rustic(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(serve, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests;
