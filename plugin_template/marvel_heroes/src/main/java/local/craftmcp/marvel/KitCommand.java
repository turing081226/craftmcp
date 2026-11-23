package local.craftmcp.marvel;

import local.craftmcp.marvel.ironman.IronManSuit;
import local.craftmcp.marvel.spiderman.SpiderSuit;
import local.craftmcp.marvel.thor.ThorHammer;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.Bukkit;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.command.PluginCommand;
import org.bukkit.command.TabExecutor;
import org.bukkit.entity.Player;
import org.bukkit.inventory.ItemStack;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.function.Supplier;

/**
 * {@code /kit <hero> [player]} hands out a hero suit.
 *
 * <p>Another plugin may already own {@code /kit} (e.g. SimpleKit). Once every plugin is enabled,
 * {@link #claim} makes {@code /kit} point here and remembers the previous owner: a bare {@code /kit}
 * with no hero name is passed on to it, so the old kit keeps working.
 */
public final class KitCommand implements TabExecutor {

    private record Hero(String display, String message, Supplier<ItemStack[]> suit) {
    }

    private final Map<String, Hero> heroes = new LinkedHashMap<>();
    private final Map<String, String> aliases = Map.of("아이언맨", "ironman", "스파이더맨", "spiderman", "토르", "thor");
    private final MarvelHeroesPlugin plugin;
    private Command previous;

    public KitCommand(MarvelHeroesPlugin plugin) {
        this.plugin = plugin;
        heroes.put("ironman", new Hero("아이언맨",
                "철 흉갑 = 9번 칸 리펄서, 철 부츠 = 스페이스 2번 비행 / 8번 칸 추진기", IronManSuit::createSuit));
        heroes.put("spiderman", new Hero("스파이더맨",
                "가죽 풀세트 = 공격력 4배, 벽 타기, 9번 칸 거미줄", SpiderSuit::createSuit));
        heroes.put("thor", new Hero("토르",
                "묠니르를 들고 우클릭 = 던지기, 다시 우클릭 = 되돌아오기",
                () -> new ItemStack[]{ThorHammer.create()}));
    }

    /** Points /kit at this command, keeping whatever owned it before as the no-argument fallback. */
    public void claim(PluginCommand ours) {
        Map<String, Command> known = Bukkit.getCommandMap().getKnownCommands();
        Command current = known.get("kit");
        if (current != null && current != ours) {
            previous = current;
            plugin.getLogger().info("/kit was owned by " + describe(current)
                    + "; MarvelHeroes takes it over (a bare /kit still runs the old one).");
        }
        known.put("kit", ours);
        Bukkit.getOnlinePlayers().forEach(Player::updateCommands);
    }

    private static String describe(Command c) {
        return c instanceof PluginCommand pc ? pc.getPlugin().getName() : c.getClass().getSimpleName();
    }

    @Override
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (args.length == 0) {
            if (previous != null) return previous.execute(sender, label, args);
            usage(sender);
            return true;
        }
        String key = args[0].toLowerCase(Locale.ROOT);
        Hero hero = heroes.get(aliases.getOrDefault(key, key));
        if (hero == null) {
            usage(sender);
            return true;
        }
        Player target;
        if (args.length >= 2) {
            if (!sender.hasPermission("marvel.kit.others")) {
                sender.sendMessage(Component.text("다른 플레이어에게 주려면 권한이 필요합니다.", NamedTextColor.RED));
                return true;
            }
            target = Bukkit.getPlayerExact(args[1]);
        } else {
            target = sender instanceof Player p ? p : null;
        }
        if (target == null) {
            sender.sendMessage(Component.text("대상 플레이어를 찾을 수 없습니다.", NamedTextColor.RED));
            return true;
        }
        for (ItemStack piece : hero.suit().get()) {
            target.getInventory().addItem(piece).values()
                    .forEach(left -> target.getWorld().dropItem(target.getLocation(), left));
        }
        target.sendMessage(Component.text(hero.display() + " 슈트를 받았습니다. " + hero.message(), NamedTextColor.GOLD));
        if (target != sender) {
            sender.sendMessage(Component.text(target.getName() + "에게 " + hero.display() + " 슈트를 지급했습니다.",
                    NamedTextColor.GREEN));
        }
        return true;
    }

    private void usage(CommandSender sender) {
        sender.sendMessage(Component.text("/kit <" + String.join("|", heroes.keySet()) + "> [플레이어]",
                NamedTextColor.YELLOW));
    }

    @Override
    public List<String> onTabComplete(CommandSender sender, Command command, String alias, String[] args) {
        List<String> out = new ArrayList<>();
        if (args.length == 1) {
            for (String h : heroes.keySet()) if (h.startsWith(args[0].toLowerCase(Locale.ROOT))) out.add(h);
        } else if (args.length == 2 && sender.hasPermission("marvel.kit.others")) {
            for (Player p : Bukkit.getOnlinePlayers()) {
                if (p.getName().toLowerCase(Locale.ROOT).startsWith(args[1].toLowerCase(Locale.ROOT))) out.add(p.getName());
            }
        }
        return out;
    }
}
