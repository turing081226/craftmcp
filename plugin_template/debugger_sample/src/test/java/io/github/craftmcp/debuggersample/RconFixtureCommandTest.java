package io.github.craftmcp.debuggersample;

import java.lang.reflect.Proxy;
import java.util.ArrayList;
import java.util.List;
import org.bukkit.command.CommandSender;
import org.bukkit.command.ConsoleCommandSender;
import org.bukkit.command.RemoteConsoleCommandSender;
import org.bukkit.entity.Player;
import org.junit.jupiter.api.Test;

/** Real Paper API interfaces, synthetic senders; actual RCON is checked by the Paper probe. */
public final class RconFixtureCommandTest {
    @Test
    void validatesSendersAndOutputs() {
        RconFixtureCommand fixture = new RconFixtureCommand();
        List<String> output = new ArrayList<>();
        for (Class<?> type : List.of(ConsoleCommandSender.class, Player.class, CommandSender.class)) {
            output.clear();
            fixture.onCommand(sender(type, output), null, "craftdebugrcon", new String[]{"calculate", "run-1", "7", "3"});
            require(output.equals(List.of("CRAFTDEBUG_RCON v=1 status=error run_id=run-1 code=REMOTE_CONSOLE_REQUIRED end=1\n")),
                    "Non-RCON sender was not rejected: " + type);
        }
        CommandSender remote = sender(RemoteConsoleCommandSender.class, output);
        output.clear();
        fixture.onCommand(remote, null, "craftdebugrcon", new String[]{"calculate", "run-1", "7", "3"});
        require(output.equals(List.of(
                "CRAFTDEBUG_RCON v=1 status=started run_id=run-1 base=7 bonus=3\n",
                "CRAFTDEBUG_RCON v=1 status=ok run_id=run-1 base=7 bonus=3 damage=10 end=1\n")), "Wrong success output");
        for (String[] values : List.of(new String[]{"calculate", "run-1", "2147483647", "1"},
                new String[]{"calculate", "run-1", "x", "3"}, new String[]{"calculate", "bad run", "7", "3"},
                new String[]{}, new String[]{"calculate", "run-1", "7", "3", "extra"})) {
            output.clear();
            fixture.onCommand(remote, null, "craftdebugrcon", values);
            String last = output.get(output.size() - 1);
            require(last.contains("status=error") && last.endsWith(" end=1\n"), "Missing terminal error");
            require(output.stream().noneMatch(line -> line.contains("status=ok")), "Error returned success");
        }
        System.out.println("Sender checks passed: RCON allowed; console/player/generic rejected; validation/overflow checked.");
    }

    private static CommandSender sender(Class<?> type, List<String> output) {
        return (CommandSender) Proxy.newProxyInstance(type.getClassLoader(), new Class<?>[]{type}, (proxy, method, args) -> {
            if (method.getName().equals("sendMessage") && args.length == 1 && args[0] instanceof String text) {
                output.add(text);
                return null;
            }
            throw new AssertionError("Unexpected sender interaction: " + method);
        });
    }

    private static void require(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
}
