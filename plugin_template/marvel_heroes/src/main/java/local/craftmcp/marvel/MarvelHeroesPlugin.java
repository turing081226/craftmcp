package local.craftmcp.marvel;

import local.craftmcp.marvel.ironman.FlightBoots;
import local.craftmcp.marvel.ironman.IronManSuit;
import local.craftmcp.marvel.ironman.RepulsorGauntlet;
import local.craftmcp.marvel.spiderman.SpiderStrength;
import local.craftmcp.marvel.spiderman.SpiderSuit;
import local.craftmcp.marvel.spiderman.WallCrawl;
import local.craftmcp.marvel.spiderman.WebShooter;
import local.craftmcp.marvel.thor.Mjolnir;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.Bukkit;
import org.bukkit.GameMode;
import org.bukkit.command.PluginCommand;
import org.bukkit.entity.Player;
import org.bukkit.event.Listener;
import org.bukkit.plugin.java.JavaPlugin;

public final class MarvelHeroesPlugin extends JavaPlugin {

    private ResourcePackHost packHost;
    private SuitSlots slots;
    private FlightBoots boots;
    private WebShooter webs;
    private WallCrawl crawl;
    private Mjolnir mjolnir;

    @Override
    public void onEnable() {
        saveDefaultConfig();

        packHost = new ResourcePackHost(this);
        packHost.start();
        listen(packHost);

        // Locked hotbar slots of every hero
        slots = new SuitSlots(this);
        slots.register(IronManSuit.REPULSOR_SLOT, IronManSuit::wearsChestplate, IronManSuit::createRepulsor,
                IronManSuit::isRepulsor, "9번 칸은 리펄서 전용입니다. 기존 아이템을 옮겼습니다.");
        slots.register(IronManSuit.THRUSTER_SLOT,
                p -> IronManSuit.wearsBoots(p) && p.getGameMode() != GameMode.SPECTATOR,
                IronManSuit::createThruster, IronManSuit::isThruster,
                "8번 칸은 추진기 전용입니다. 기존 아이템을 옮겼습니다.");
        slots.register(SpiderSuit.WEB_SLOT, SpiderSuit::wearsSuit, SpiderSuit::createWebShooter,
                SpiderSuit::isWebShooter, "9번 칸은 웹 슈터 전용입니다. 기존 아이템을 옮겼습니다.");
        slots.start();
        listen(slots);

        // Iron Man
        listen(new RepulsorGauntlet(this));
        boots = new FlightBoots(this);
        boots.start();
        listen(boots);

        // Spider-Man
        listen(new SpiderStrength(this));
        webs = new WebShooter(this);
        webs.start();
        listen(webs);
        crawl = new WallCrawl(this, webs::isZipping);
        crawl.start();

        // Thor
        mjolnir = new Mjolnir(this);
        mjolnir.start();
        listen(mjolnir);

        KitCommand kit = new KitCommand(this);
        PluginCommand kitCommand = getCommand("kit");
        if (kitCommand != null) {
            kitCommand.setExecutor(kit);
            kitCommand.setTabCompleter(kit);
            // Runs once every plugin is enabled, so /kit can be taken over from whoever registered it.
            Bukkit.getScheduler().runTask(this, () -> kit.claim(kitCommand));
        }
        PluginCommand pack = getCommand("marvelpack");
        if (pack != null) {
            pack.setExecutor((sender, cmd, label, args) -> {
                if (!(sender instanceof Player p)) {
                    sender.sendMessage("플레이어만 사용할 수 있습니다.");
                } else if (!packHost.available()) {
                    sender.sendMessage(Component.text("리소스팩이 비활성화되어 있습니다(config.yml 확인).", NamedTextColor.RED));
                } else {
                    packHost.send(p);
                }
                return true;
            });
        }

        getLogger().info("MarvelHeroes enabled: Iron Man (iron armor), Spider-Man (full leather armor), Thor (Mjolnir).");
    }

    @Override
    public void onDisable() {
        if (slots != null) slots.shutdown();
        if (boots != null) boots.shutdown();
        if (webs != null) webs.shutdown();
        if (crawl != null) crawl.shutdown();
        if (mjolnir != null) mjolnir.shutdown();
        if (packHost != null) packHost.stop();
    }

    /** Logs {@code message} when {@code debug: true} is set in config.yml. */
    public void debug(String message) {
        if (getConfig().getBoolean("debug", false)) getLogger().info(message);
    }

    private void listen(Listener listener) {
        getServer().getPluginManager().registerEvents(listener, this);
    }
}
