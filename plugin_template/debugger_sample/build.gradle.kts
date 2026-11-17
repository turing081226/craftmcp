plugins { java }

group = "io.github.craftmcp"
version = "0.1.0"

repositories {
    mavenCentral()
    maven("https://repo.papermc.io/repository/maven-public/")
}

dependencies {
    compileOnly("io.papermc.paper:paper-api:1.20.1-R0.1-SNAPSHOT")
    testImplementation("io.papermc.paper:paper-api:1.20.1-R0.1-SNAPSHOT")
    testImplementation(platform("org.junit:junit-bom:5.11.4"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

java { toolchain.languageVersion.set(JavaLanguageVersion.of(17)) }

tasks.withType<JavaCompile>().configureEach {
    options.release.set(17)
    options.encoding = "UTF-8"
    options.isDebug = true
    options.debugOptions.debugLevel = "source,lines,vars"
}

tasks.jar { archiveFileName.set("craftmcp-debugger-sample.jar") }

tasks.test { useJUnitPlatform() }
