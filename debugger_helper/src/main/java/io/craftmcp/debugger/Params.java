package io.craftmcp.debugger;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.util.HashSet;
import java.util.Set;

final class Params {
    private final JsonObject data;

    Params(JsonObject data, Set<String> required, Set<String> optional) {
        this.data = data;
        Set<String> allowed = new HashSet<>(required);
        allowed.addAll(optional);
        for (String name : required) if (!data.has(name)) throw invalid("Missing parameter: " + name);
        for (String name : data.keySet()) if (!allowed.contains(name)) throw invalid("Unknown parameter: " + name);
    }

    boolean has(String name) { return data.has(name); }

    String string(String name) {
        JsonElement value = data.get(name);
        if (value == null || !value.isJsonPrimitive() || !value.getAsJsonPrimitive().isString()
                || value.getAsString().isBlank()) throw invalid("Expected non-empty string: " + name);
        return value.getAsString();
    }

    String optionalString(String name) { return has(name) ? string(name) : null; }

    int integer(String name, int minimum, int maximum) {
        JsonElement value = data.get(name);
        if (value == null || !value.isJsonPrimitive() || !value.getAsJsonPrimitive().isNumber()
                || !value.getAsString().matches("-?(0|[1-9][0-9]*)")) throw invalid("Expected integer: " + name);
        try {
            int number = value.getAsBigDecimal().intValueExact();
            if (number < minimum || number > maximum) throw invalid("Out of range: " + name);
            return number;
        } catch (ArithmeticException exception) { throw invalid("Out of range: " + name); }
    }

    static Fault invalid(String message) { return new Fault("invalid_params", message); }
}
