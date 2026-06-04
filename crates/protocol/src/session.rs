use anyhow::{anyhow, Result};
use liqi::pb;

use crate::{
    config::{match_sid, rank_tier_from_level_id, target_mode_for_rank_level, Mode, Room},
    login::{client_version_string, login_payload},
    routes::{
        lobby_ws_url_candidates, prepare_login_body, route_body, route_ws_url, GAME_ROUTE_IDS,
    },
    transport::LiqiSocket,
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LoginSummary {
    pub account_id: u32,
    pub nickname: String,
    pub level_id: u32,
    pub rank_tier: u32,
    pub target_mode: Mode,
    pub target_room: Room,
    pub access_token: String,
}

pub struct ProtocolClient {
    socket: LiqiSocket,
    route_id: String,
    route_prep: Option<LiqiSocket>,
    pub summary: LoginSummary,
}

pub async fn check_login(username: &str, password: &str, device_id: &str) -> Result<LoginSummary> {
    Ok(ProtocolClient::login(username, password, device_id)
        .await?
        .summary)
}

impl ProtocolClient {
    pub async fn login(username: &str, password: &str, device_id: &str) -> Result<Self> {
        let (route_id, url) = first_lobby_route()?;
        let mut socket = LiqiSocket::connect(&url).await?;
        let now_ms = current_time_millis();
        socket
            .request_raw(
                ".lq.Route.requestConnection",
                &route_body(1, &route_id, now_ms),
            )
            .await?;

        let login = login_payload(username, password, device_id, false);
        let response: pb::ResLogin = socket.request(".lq.Lobby.login", &login).await?;
        let summary = login_summary(response)?;
        Ok(Self {
            socket,
            route_id,
            route_prep: None,
            summary,
        })
    }

    pub async fn start_match(&mut self) -> Result<String> {
        self.prepare_game_route().await?;
        let sid = match_sid(&self.summary.target_mode, &self.summary.target_room)
            .ok_or_else(|| anyhow!("no match sid for current target"))?;
        let response: pb::ResCommon = self
            .socket
            .request(
                ".lq.Lobby.startUnifiedMatch",
                &start_match_payload(sid.clone()),
            )
            .await?;
        if let Some(error) = response.error {
            if error.code != 0 {
                return Err(anyhow!(
                    "startUnifiedMatch failed: code={} str={:?} u32={:?}",
                    error.code,
                    error.str_params,
                    error.u32_params
                ));
            }
        }
        Ok(sid)
    }

    pub async fn cancel_match(&mut self) -> Result<()> {
        let sid = match_sid(&self.summary.target_mode, &self.summary.target_room)
            .ok_or_else(|| anyhow!("no match sid for current target"))?;
        let response: pb::ResCommon = self
            .socket
            .request(
                ".lq.Lobby.cancelUnifiedMatch",
                &pb::ReqCancelUnifiedMatch { match_sid: sid },
            )
            .await?;
        if let Some(error) = response.error {
            if error.code != 0 {
                return Err(anyhow!("cancelUnifiedMatch failed: code={}", error.code));
            }
        }
        Ok(())
    }

    async fn prepare_game_route(&mut self) -> Result<()> {
        if self.summary.access_token.is_empty() {
            return Err(anyhow!("cannot prepare route without access token"));
        }
        let mut candidates = Vec::new();
        candidates.push(self.route_id.clone());
        for route in GAME_ROUTE_IDS {
            if !candidates.iter().any(|candidate| candidate == route) {
                candidates.push(route.to_string());
            }
        }

        let mut last_error = None;
        for route in candidates {
            let url = route_ws_url(&route, "gateway");
            match self.prepare_game_route_once(&route, &url).await {
                Ok(sock) => {
                    self.route_prep = Some(sock);
                    return Ok(());
                }
                Err(err) => last_error = Some(err),
            }
        }
        Err(last_error.unwrap_or_else(|| anyhow!("prepareLogin failed for all routes")))
    }

    async fn prepare_game_route_once(&self, route: &str, url: &str) -> Result<LiqiSocket> {
        let mut socket = LiqiSocket::connect(url).await?;
        socket
            .request_raw(
                ".lq.Route.requestConnection",
                &route_body(2, route, current_time_millis()),
            )
            .await?;
        socket
            .request_raw(
                ".lq.Lobby.prepareLogin",
                &prepare_login_body(&self.summary.access_token),
            )
            .await?;
        Ok(socket)
    }
}

fn first_lobby_route() -> Result<(String, String)> {
    let (route_id, url) = lobby_ws_url_candidates()
        .into_iter()
        .find_map(|url| {
            let route_id = url.split("://").nth(1)?.split('.').next()?.to_string();
            Some((route_id, url))
        })
        .ok_or_else(|| anyhow!("no lobby route candidates"))?;
    Ok((route_id, url))
}

fn login_summary(response: pb::ResLogin) -> Result<LoginSummary> {
    if let Some(error) = response.error {
        if error.code != 0 {
            return Err(anyhow!(
                "login failed: code={} str={:?} u32={:?}",
                error.code,
                error.str_params,
                error.u32_params
            ));
        }
    }

    let account = response
        .account
        .ok_or_else(|| anyhow!("login response missing account"))?;
    let level = account
        .level
        .ok_or_else(|| anyhow!("login response missing four-player level"))?;
    let (target_mode, target_room) = target_mode_for_rank_level(level.id);

    Ok(LoginSummary {
        account_id: response.account_id,
        nickname: account.nickname,
        level_id: level.id,
        rank_tier: rank_tier_from_level_id(level.id),
        target_mode,
        target_room,
        access_token: response.access_token,
    })
}

pub fn start_match_payload(match_sid: String) -> pb::ReqStartUnifiedMatch {
    pb::ReqStartUnifiedMatch {
        match_sid,
        client_version_string: client_version_string(),
    }
}

fn current_time_millis() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn start_match_payload_uses_current_webgl_client_version() {
        let payload = start_match_payload("1:9".to_string());
        assert_eq!(payload.match_sid, "1:9");
        assert_eq!(payload.client_version_string, "WebGL_2022-0.16.229");
    }
}
