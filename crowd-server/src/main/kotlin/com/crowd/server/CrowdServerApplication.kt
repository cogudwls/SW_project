package com.crowd.server

import org.springframework.boot.autoconfigure.SpringBootApplication
import org.springframework.boot.runApplication

@SpringBootApplication
class CrowdServerApplication

fun main(args: Array<String>) {
    runApplication<CrowdServerApplication>(*args)
}