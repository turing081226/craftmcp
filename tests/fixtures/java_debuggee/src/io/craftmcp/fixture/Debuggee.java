package io.craftmcp.fixture;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;

/** Keeps Calculator unloaded until the first command, for pending-breakpoint tests. */
public final class Debuggee {
    public static void main(String[] args) throws Exception {
        var input = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        System.out.println("READY");
        String command;
        while ((command = input.readLine()) != null && !command.equals("quit")) {
            switch (command) {
                case "run" -> new Calculator().calculate(4, 3);
                case "parallel" -> {
                    Thread first = new Thread(() -> new Calculator().calculate(4, 3), "fixture-first");
                    Thread second = new Thread(() -> new Calculator().calculate(2, 5), "fixture-second");
                    first.start();
                    second.start();
                }
                case "duplicate" -> {
                    String name = "io.craftmcp.fixture.Calculator";
                    byte[] bytes;
                    try (var stream = Debuggee.class.getResourceAsStream("Calculator.class")) {
                        bytes = stream.readAllBytes();
                    }
                    ClassLoader loader = new ClassLoader(Debuggee.class.getClassLoader()) {
                        @Override protected Class<?> loadClass(String target, boolean resolve) throws ClassNotFoundException {
                            if (!target.equals(name)) return super.loadClass(target, resolve);
                            Class<?> type = findLoadedClass(target);
                            if (type == null) type = defineClass(target, bytes, 0, bytes.length);
                            if (resolve) resolveClass(type);
                            return type;
                        }
                    };
                    Class.forName(name, true, loader).getConstructor().newInstance();
                    System.out.println("DUPLICATE_LOADED");
                }
                default -> System.out.println("UNKNOWN_COMMAND");
            }
        }
        System.out.println("BYE");
    }
}
