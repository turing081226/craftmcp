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

tasks.processResources {
    filesMatching("plugin.yml") { expand("version" to project.version) }
}

tasks.jar { archiveFileName.set("BlazeFireball.jar") }
