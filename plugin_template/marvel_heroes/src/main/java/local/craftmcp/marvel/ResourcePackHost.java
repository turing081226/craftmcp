package local.craftmcp.marvel;

import com.sun.net.httpserver.HttpServer;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.player.PlayerResourcePackStatusEvent;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.concurrent.Executors;
import java.util.logging.Level;

/**
 * Serves the bundled Iron Man resource pack (marvel_pack.zip inside the plugin jar) over a tiny
 * built-in HTTP server and pushes it to players when they join.
 */
public final class ResourcePackHost implements Listener {

    private static final String PACK_RESOURCE = "marvel_pack.zip";

    private final MarvelHeroesPlugin plugin;
    private byte[] pack;
    private byte[] sha1;
    private String url;
    private HttpServer http;

    public ResourcePackHost(MarvelHeroesPlugin plugin) {
        this.plugin = plugin;
    }

    public void start() {
        if (!plugin.getConfig().getBoolean("resource-pack.enabled", true)) return;
        try (InputStream in = plugin.getResource(PACK_RESOURCE)) {
            if (in == null) {
                plugin.getLogger().warning(PACK_RESOURCE + " is missing from the jar; textures disabled.");
                return;
            }
            pack = in.readAllBytes();
            sha1 = MessageDigest.getInstance("SHA-1").digest(pack);
        } catch (IOException | NoSuchAlgorithmException e) {
            plugin.getLogger().log(Level.WARNING, "Could not load resource pack", e);
            return;
        }
        String hex = HexFormat.of().formatHex(sha1);

        String external = plugin.getConfig().getString("resource-pack.url", "");
        if (external != null && !external.isBlank()) {
            url = external;
            plugin.getLogger().info("Using external resource pack URL " + url + " (sha1 " + hex + ")");
            return;
        }

        int port = plugin.getConfig().getInt("resource-pack.port", 8765);
        String host = plugin.getConfig().getString("resource-pack.public-host", "127.0.0.1");
        try {
            http = HttpServer.create(new InetSocketAddress(port), 0);
            http.createContext("/marvel_pack.zip", exchange -> {
                try {
                    exchange.getResponseHeaders().set("Content-Type", "application/zip");
                    exchange.sendResponseHeaders(200, pack.length);
                    try (OutputStream out = exchange.getResponseBody()) {
                        out.write(pack);
                    }
                } finally {
                    exchange.close();
                }
            });
            http.setExecutor(Executors.newSingleThreadExecutor(r -> {
                Thread t = new Thread(r, "MarvelHeroes-ResourcePack");
                t.setDaemon(true);
                return t;
            }));
            http.start();
            // The hash in the query string busts the client cache whenever the pack changes.
            url = "http://" + host + ":" + port + "/marvel_pack.zip?v=" + hex.substring(0, 12);
            plugin.getLogger().info("Serving hero resource pack at " + url);
        } catch (IOException e) {
            plugin.getLogger().log(Level.WARNING, "Could not start resource pack HTTP server on port " + port, e);
            http = null;
            url = null;
        }
    }

    public void stop() {
        if (http != null) {
            http.stop(0);
            http = null;
        }
    }

    public boolean available() {
        return url != null;
    }

    public void send(Player player) {
        if (url == null) return;
        boolean force = plugin.getConfig().getBoolean("resource-pack.force", false);
        player.setResourcePack(url, sha1,
                Component.text("마블 히어로 슈트 텍스처(아이언맨·스파이더맨)를 적용합니다.", NamedTextColor.GOLD), force);
    }

    @EventHandler
    public void onJoin(PlayerJoinEvent event) {
        Player player = event.getPlayer();
        // Give the client a moment to finish logging in before the pack prompt appears.
        plugin.getServer().getScheduler().runTaskLater(plugin, () -> {
            if (player.isOnline()) send(player);
        }, 20L);
    }

    @EventHandler
    public void onStatus(PlayerResourcePackStatusEvent event) {
        if (event.getStatus() == PlayerResourcePackStatusEvent.Status.DECLINED
                || event.getStatus() == PlayerResourcePackStatusEvent.Status.FAILED_DOWNLOAD) {
            event.getPlayer().sendMessage(Component.text(
                    "아이언맨 텍스처를 받지 못했습니다(" + event.getStatus() + "). /marvelpack 으로 다시 받을 수 있습니다.",
                    NamedTextColor.YELLOW));
        }
    }
}
