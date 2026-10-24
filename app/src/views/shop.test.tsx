import { describe, expect, it } from "vitest";
import { formatPrice, type Product } from "./Shop";

const product: Product = {
  title: "Headphones X",
  price: 129.9,
  currency: "EUR",
  shop: "amazon.de",
  url: "https://www.amazon.de/dp/1",
  summary: "",
  specs: [],
  availability: "",
};

describe("shop helpers", () => {
  it("formats prices and missing prices", () => {
    expect(formatPrice(product)).toBe("129.90 EUR");
    expect(formatPrice({ ...product, price: null })).toBe("Price unknown");
  });
});
