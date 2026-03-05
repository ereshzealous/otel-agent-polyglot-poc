package com.shopflow.order.model;

import jakarta.validation.constraints.*;
import java.math.BigDecimal;

public class CreateOrderRequest {
    @NotBlank  private String customerId;
    @NotBlank  private String productId;
    @Min(1)    private Integer quantity;
    @Positive  private BigDecimal unitPrice;

    public String getCustomerId() { return customerId; }
    public void setCustomerId(String customerId) { this.customerId = customerId; }
    public String getProductId() { return productId; }
    public void setProductId(String productId) { this.productId = productId; }
    public Integer getQuantity() { return quantity; }
    public void setQuantity(Integer quantity) { this.quantity = quantity; }
    public BigDecimal getUnitPrice() { return unitPrice; }
    public void setUnitPrice(BigDecimal unitPrice) { this.unitPrice = unitPrice; }
}
