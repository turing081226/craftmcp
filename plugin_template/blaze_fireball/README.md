# BlazeFireball 플러그인

블레이즈 막대기를 주 손에 들고 우클릭하면 바라보는 방향으로 연출이 붙은 큰 파이어볼을 발사한다.
JDK 17, Paper API 1.20.1, Gradle Wrapper 8.14.3.

## 동작

| 단계 | 연출 |
|---|---|
| 발사 | 발밑에서 퍼지는 불꽃 고리, 손 앞 섬광·용암 파티클, 블레이즈·가스트 발사음과 화염구 사용음 |
| 비행 | 두 줄 나선 불꽃, 작은 불꽃, 큰 연기, 주황색 더스트 꼬리 (최대 10초) |
| 명중 | 대형 폭발 이펙트, 불꽃·용암·모닥불 연기, 폭발음과 블레이즈 사망음 |

- 허공·블록 우클릭 모두 발동하며 원래의 블록 상호작용은 취소한다.
- 플레이어별 쿨다운 0.8초, 속도 1.6블록/틱, 폭발 위력 2.5, 주변에 불이 붙는다(블록 파괴 있음).
- 권한 `blazefireball.use` (기본값: 모든 플레이어 허용).
- 수치는 [BlazeFireballPlugin.java](src/main/java/local/craftmcp/blazefireball/BlazeFireballPlugin.java)
  상단의 `COOLDOWN_MS`, `SPEED`와 `setYield(...)`에서 조절한다.

## 빌드

저장소 루트에서:

```powershell
.\plugin_template\blaze_fireball\gradlew.bat -p plugin_template/blaze_fireball build
```

```sh
sh plugin_template/blaze_fireball/gradlew -p plugin_template/blaze_fireball build
```

JAR: `plugin_template/blaze_fireball/build/libs/BlazeFireball.jar`.
첫 빌드는 Gradle 배포판과 Paper API를 내려받으므로 수 분이 걸릴 수 있다.

## 설치와 확인

1. JAR를 `mc_server/plugins/`에 복사하고 Paper 서버를 (재)시작한다.
2. 서버 로그에 `[BlazeFireball] BlazeFireball enabled: right-click with a blaze rod!`가 출력되는지 확인한다.
3. 게임에서 `/give @s blaze_rod`로 막대기를 받아 들고 우클릭한다.

CraftMCP 도구로 확인할 때는 `run_game_chat_command`로 막대기를 지급하고
`send_game_input`(`{"type": "click", "button": "right"}`)으로 발사한 뒤 `capture_game_view`로
비행·폭발 장면을 캡처한다. 서버가 청크를 불러오는 중(`Can't keep up!`)이면 발사가 늦게 처리될 수 있다.

MCP `build_plugin`에 이 소스를 그대로 넘겨 빌드할 수도 있다
(`plugin_name=BlazeFireball`, `main_class=local.craftmcp.blazefireball.BlazeFireballPlugin`,
manifest의 permissions는 위 `plugin.yml`과 동일). 의존성 캐시가 준비되지 않은 첫 빌드는 기본
180초 기한을 넘길 수 있으므로 `offline=False`로 캐시를 먼저 준비하거나 `timeout_seconds`를 늘린다.
