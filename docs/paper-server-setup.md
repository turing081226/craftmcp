# Paper 테스트 서버 준비

CraftMCP가 사용하는 로컬 테스트 서버를 만든다. 버전은 [기술 결정](decisions.md)에 따라
**Paper 1.20.1 빌드 196**, **Java 17**이다. 서버 실행 파일과 월드 데이터는 저장소에 커밋하지 않는다.

## 1. Java 17 확인

```powershell
java -version   # 17.x 이어야 한다
```

없으면 `winget install EclipseAdoptium.Temurin.17.JDK`로 설치하고 터미널을 다시 연다.

## 2. paper.jar 내려받기

[PaperMC 다운로드 페이지](https://papermc.io/downloads/all)에서 버전 1.20.1, 빌드 196을 받아
`mc_server/paper.jar`라는 이름으로 저장한다.

## 3. 서버 설정

```powershell
copy mc_server\server.properties.example mc_server\server.properties
```

`mc_server/server.properties`에서 RCON 값을 확인한다.

```properties
enable-rcon=true
rcon.port=25575
rcon.password=각자_정한_비밀번호
```

- **`rcon.password`는 예시 값을 그대로 쓰지 말고 각자 바꾼다.** 같은 값을 `mcp_server/.env`의 `MC_RCON_PASSWORD`에 넣는다.
- `rcon.port`를 바꾸면 `.env`의 `MC_RCON_PORT`도 같이 바꾼다.

## 4. 첫 실행과 EULA

```powershell
mc_server\start.bat        # macOS/Linux: sh mc_server/start.sh
```

첫 실행은 EULA 때문에 바로 멈춘다. 생성된 `mc_server/eula.txt`에서 `eula=false`를 `eula=true`로 바꾸고
다시 실행한다([Minecraft EULA](https://aka.ms/MinecraftEULA)에 동의하는 것이다).
`Done (...)! For help, type "help"`가 보이면 준비 완료다. `start.bat`은 서버가 꺼지면 5초 뒤 자동으로 다시 켠다.

## 5. 플러그인 넣기

빌드한 JAR를 `mc_server/plugins/`에 넣고 서버를 재시작한다. MCP의 `build_plugin` → `deploy_plugin`을
쓰면 AI가 이 과정을 대신한다([플러그인 빌드·배포](plugin-build-deploy.md)).

## 6. 게임에서 접속

Minecraft Java **1.20.1**로 멀티플레이 → 서버 주소 `localhost`로 접속한다.
캡처·게임 조작 기능을 쓰려면 이 게임을 MCP 서버와 같은 PC에서 실행한다.

## 디버거용 실행

JVM 디버거를 붙이려면 `mc_server\start-debug.bat`(JDWP 5005 포트)으로 실행한다. 자세한 내용은
[디버거 안내](debugger-python.md)를 본다.
