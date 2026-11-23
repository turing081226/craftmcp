plugins { java }

group = "local.craftmcp"
version = "1.0.0"

repositories {
    mavenCentral()
    maven("https://repo.papermc.io/repository/maven-public/")
}

dependencies {
    compileOnly("io.papermc.paper:paper-api:1.20.1-R0.1-SNAPSHOT")
}

java { toolchain.languageVersion.set(JavaLanguageVersion.of(17)) }

tasks.withType<JavaCompile>().configureEach {
    options.release.set(17)
    options.encoding = "UTF-8"
    options.isDebug = true
    options.debugOptions.debugLevel = "source,lines,vars"
}

// The Iron Man resource pack is zipped into the plugin jar; the plugin serves it to players.
val resourcePackZip = tasks.register<Zip>("resourcePackZip") {
    from("resourcepack")
    archiveFileName.set("marvel_pack.zip")
    destinationDirectory.set(layout.buildDirectory.dir("generated/pack"))
    isPreserveFileTimestamps = false
    isReproducibleFileOrder = true
}

tasks.processResources {
    filesMatching("plugin.yml") { expand("version" to project.version) }
    from(resourcePackZip)
}

tasks.jar { archiveFileName.set("MarvelHeroes.jar") }
