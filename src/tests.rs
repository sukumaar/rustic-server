use super::*;
use axum::{
    body::{to_bytes, Body},
    http::Request,
};
use tower::ServiceExt;

fn route(method: &str, path: &str, status: u16, body: &str) -> Route {
    (method.into(), path.into(), status, body.as_bytes().to_vec())
}

#[tokio::test]
async fn serves_json_and_http_method_semantics() {
    let router = build_router(vec![
        route("GET", "/health", 200, r#"{"status":"ok"}"#),
        route("POST", "/health", 202, r#"{"accepted":true}"#),
    ])
    .unwrap();
    for (method, path, status, expected) in [
        ("GET", "/health?check=1", 200, Some(r#"{"status":"ok"}"#)),
        ("POST", "/health", 202, Some(r#"{"accepted":true}"#)),
        ("HEAD", "/health", 200, Some("")),
        ("DELETE", "/health", 405, None),
        ("GET", "/missing", 404, None),
    ] {
        let response = router
            .clone()
            .oneshot(
                Request::builder()
                    .method(method)
                    .uri(path)
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status().as_u16(), status);
        if let Some(expected) = expected {
            assert_eq!(response.headers()[header::CONTENT_TYPE], "application/json");
            assert_eq!(
                to_bytes(response.into_body(), 1024).await.unwrap().as_ref(),
                expected.as_bytes()
            );
        }
    }
}

#[test]
fn rejects_invalid_native_arguments() {
    for routes in [
        vec![],
        vec![route("GET", "/{id}", 200, "null")],
        vec![route("GET", "/", 204, "null")],
        vec![route("UNKNOWN", "/", 200, "null")],
        vec![route("GET", "/", 200, "invalid json")],
        vec![
            route("GET", "/", 200, "null"),
            route("GET", "/", 200, "null"),
        ],
    ] {
        assert!(build_router(routes).is_err());
    }
}
