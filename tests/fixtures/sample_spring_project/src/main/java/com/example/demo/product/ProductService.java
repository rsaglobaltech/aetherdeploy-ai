package com.example.demo.product;

import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

@Service
@Transactional(readOnly = true)
public class ProductService {

    private final ProductRepository repository;

    public ProductService(ProductRepository repository) {
        this.repository = repository;
    }

    public List<Product> findAll() {
        return repository.findAll();
    }

    public Product findById(Long id) {
        return repository.findById(id)
                .orElseThrow(() -> new ProductNotFoundException(id));
    }

    public List<Product> search(String name) {
        return repository.findByNameContainingIgnoreCase(name);
    }

    @Transactional
    public Product create(Product product) {
        return repository.save(product);
    }

    @Transactional
    public Product update(Long id, Product updates) {
        Product existing = findById(id);
        existing.setName(updates.getName());
        existing.setDescription(updates.getDescription());
        existing.setPrice(updates.getPrice());
        existing.setStock(updates.getStock());
        return repository.save(existing);
    }

    @Transactional
    public void delete(Long id) {
        repository.delete(findById(id));
    }
}
