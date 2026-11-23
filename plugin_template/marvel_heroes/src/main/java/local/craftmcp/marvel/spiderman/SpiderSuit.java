package local.craftmcp.marvel.spiderman;

import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import net.kyori.adventure.text.format.TextDecoration;
import org.bukkit.Color;
import org.bukkit.GameMode;
import org.bukkit.Material;
import org.bukkit.NamespacedKey;
import org.bukkit.entity.Player;
import org.bukkit.inventory.ItemFlag;
import org.bukkit.inventory.ItemStack;
import org.bukkit.inventory.PlayerInventory;
import org.bukkit.inventory.meta.ItemMeta;
import org.bukkit.inventory.meta.LeatherArmorMeta;
import org.bukkit.persistence.PersistentDataType;

import java.util.List;

/** Spider-Man = a full set of leather armour (any colour). */
public final class SpiderSuit {

    /** Hotbar slot 9 (index 8) holds the web shooter while the suit is worn. It looks empty. */
    public static final int WEB_SLOT = 8;
    public static final int WEB_MODEL = 7003;

    private static final NamespacedKey WEB_KEY = new NamespacedKey("marvelheroes", "web_shooter");
    private static final Color RED = Color.fromRGB(0xB3, 0x16, 0x1C);
    private static final Color BLUE = Color.fromRGB(0x1B, 0x3C, 0x8C);

    private SpiderSuit() {
    }

    public static boolean wearsSuit(Player p) {
        if (p.getGameMode() == GameMode.SPECTATOR) return false;
        PlayerInventory inv = p.getInventory();
        return is(inv.getHelmet(), Material.LEATHER_HELMET)
                && is(inv.getChestplate(), Material.LEATHER_CHESTPLATE)
                && is(inv.getLeggings(), Material.LEATHER_LEGGINGS)
                && is(inv.getBoots(), Material.LEATHER_BOOTS);
    }

    private static boolean is(ItemStack item, Material type) {
        return item != null && item.getType() == type;
    }

    public static ItemStack createWebShooter() {
        ItemStack item = new ItemStack(Material.PAPER);
        ItemMeta meta = item.getItemMeta();
        meta.displayName(plain("웹 슈터 - 우클릭: 거미줄 / 다시 우클릭: 당기기·이동", NamedTextColor.WHITE));
        meta.lore(List.of(plain("가죽 갑옷 풀세트 착용 중 9번 칸에 고정됩니다", NamedTextColor.DARK_GRAY)));
        meta.setCustomModelData(WEB_MODEL);
        meta.addItemFlags(ItemFlag.HIDE_ATTRIBUTES);
        meta.getPersistentDataContainer().set(WEB_KEY, PersistentDataType.BYTE, (byte) 1);
        item.setItemMeta(meta);
        return item;
    }

    public static boolean isWebShooter(ItemStack item) {
        if (item == null || item.getType() != Material.PAPER || !item.hasItemMeta()) return false;
        return item.getItemMeta().getPersistentDataContainer().has(WEB_KEY, PersistentDataType.BYTE);
    }

    /** Red and blue dyed leather. Any leather set works; the colours are cosmetic. */
    public static ItemStack[] createSuit() {
        return new ItemStack[]{
                dyed(Material.LEATHER_HELMET, "스파이더맨 마스크", RED),
                dyed(Material.LEATHER_CHESTPLATE, "스파이더맨 슈트", RED),
                dyed(Material.LEATHER_LEGGINGS, "스파이더맨 레깅스", BLUE),
                dyed(Material.LEATHER_BOOTS, "스파이더맨 부츠", RED),
        };
    }

    private static ItemStack dyed(Material material, String name, Color color) {
        ItemStack item = new ItemStack(material);
        LeatherArmorMeta meta = (LeatherArmorMeta) item.getItemMeta();
        meta.setColor(color);
        meta.displayName(plain(name, NamedTextColor.RED));
        meta.addItemFlags(ItemFlag.HIDE_DYE);
        item.setItemMeta(meta);
        return item;
    }

    static Component plain(String text, NamedTextColor color) {
        return Component.text(text, color).decoration(TextDecoration.ITALIC, false);
    }
}
