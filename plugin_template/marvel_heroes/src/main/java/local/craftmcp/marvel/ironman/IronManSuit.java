package local.craftmcp.marvel.ironman;

import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import net.kyori.adventure.text.format.TextDecoration;
import org.bukkit.GameMode;
import org.bukkit.Material;
import org.bukkit.NamespacedKey;
import org.bukkit.enchantments.Enchantment;
import org.bukkit.entity.Player;
import org.bukkit.inventory.ItemFlag;
import org.bukkit.inventory.ItemStack;
import org.bukkit.inventory.meta.ItemMeta;
import org.bukkit.persistence.PersistentDataType;

import java.util.List;

/** Shared definitions for the Iron Man suit: which vanilla items count as the suit, and the repulsor item. */
public final class IronManSuit {

    /** Hotbar slot 9 (index 8) is reserved for the repulsor while the chestplate is worn. */
    public static final int REPULSOR_SLOT = 8;
    /** Matches the custom_model_data override in the resource pack. */
    public static final int REPULSOR_MODEL = 7001;

    /**
     * Hotbar slot 8 (index 7) is reserved for the thruster while the boots are worn. The thruster item
     * has a transparent texture, so the slot looks empty; a real empty hand cannot be used because the
     * client never tells the server about a right-click into the air with nothing in hand.
     */
    public static final int THRUSTER_SLOT = 7;
    public static final int THRUSTER_MODEL = 7002;

    private static final NamespacedKey REPULSOR_KEY = new NamespacedKey("marvelheroes", "repulsor");
    private static final NamespacedKey THRUSTER_KEY = new NamespacedKey("marvelheroes", "thruster");

    private IronManSuit() {
    }

    public static boolean wearsChestplate(Player p) {
        ItemStack chest = p.getInventory().getChestplate();
        return chest != null && chest.getType() == Material.IRON_CHESTPLATE
                && p.getGameMode() != GameMode.SPECTATOR;
    }

    public static boolean wearsBoots(Player p) {
        ItemStack feet = p.getInventory().getBoots();
        return feet != null && feet.getType() == Material.IRON_BOOTS;
    }

    public static ItemStack createRepulsor() {
        ItemStack item = new ItemStack(Material.HEART_OF_THE_SEA);
        ItemMeta meta = item.getItemMeta();
        meta.displayName(plain("리펄서", NamedTextColor.AQUA).decorate(TextDecoration.BOLD));
        meta.lore(List.of(
                plain("우클릭: 리펄서 발사", NamedTextColor.GRAY),
                plain("철 흉갑 착용 중 9번 칸에 고정됩니다", NamedTextColor.DARK_GRAY)));
        meta.setCustomModelData(REPULSOR_MODEL);
        meta.addEnchant(Enchantment.LUCK, 1, true);
        meta.addItemFlags(ItemFlag.HIDE_ENCHANTS, ItemFlag.HIDE_ATTRIBUTES);
        meta.getPersistentDataContainer().set(REPULSOR_KEY, PersistentDataType.BYTE, (byte) 1);
        item.setItemMeta(meta);
        return item;
    }

    public static boolean isRepulsor(ItemStack item) {
        return tagged(item, Material.HEART_OF_THE_SEA, REPULSOR_KEY);
    }

    public static ItemStack createThruster() {
        ItemStack item = new ItemStack(Material.FIREWORK_STAR);
        ItemMeta meta = item.getItemMeta();
        meta.displayName(plain("추진기 - 비행 중 우클릭: 가속", NamedTextColor.BLUE));
        meta.lore(List.of(plain("철 부츠 착용 중 8번 칸에 고정됩니다", NamedTextColor.DARK_GRAY)));
        meta.setCustomModelData(THRUSTER_MODEL);
        meta.addItemFlags(ItemFlag.HIDE_ATTRIBUTES, ItemFlag.HIDE_ITEM_SPECIFICS);
        meta.getPersistentDataContainer().set(THRUSTER_KEY, PersistentDataType.BYTE, (byte) 1);
        item.setItemMeta(meta);
        return item;
    }

    public static boolean isThruster(ItemStack item) {
        return tagged(item, Material.FIREWORK_STAR, THRUSTER_KEY);
    }

    private static boolean tagged(ItemStack item, Material type, NamespacedKey key) {
        if (item == null || item.getType() != type || !item.hasItemMeta()) return false;
        return item.getItemMeta().getPersistentDataContainer().has(key, PersistentDataType.BYTE);
    }

    /** A full set of named iron armour pieces. Any iron armour works; the names are cosmetic. */
    public static ItemStack[] createSuit() {
        return new ItemStack[]{
                named(Material.IRON_HELMET, "Mark 헬멧"),
                named(Material.IRON_CHESTPLATE, "Mark 흉갑 (리펄서)"),
                named(Material.IRON_LEGGINGS, "Mark 레깅스"),
                named(Material.IRON_BOOTS, "Mark 부츠 (비행)"),
        };
    }

    private static ItemStack named(Material material, String name) {
        ItemStack item = new ItemStack(material);
        ItemMeta meta = item.getItemMeta();
        meta.displayName(plain(name, NamedTextColor.GOLD));
        item.setItemMeta(meta);
        return item;
    }

    static Component plain(String text, NamedTextColor color) {
        return Component.text(text, color).decoration(TextDecoration.ITALIC, false);
    }
}
