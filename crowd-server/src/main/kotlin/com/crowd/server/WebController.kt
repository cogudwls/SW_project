package com.crowd.server

import org.springframework.http.ResponseEntity
import org.springframework.stereotype.Controller
import org.springframework.web.bind.annotation.GetMapping
import org.springframework.web.bind.annotation.PostMapping
import org.springframework.web.bind.annotation.RequestBody
import org.springframework.web.bind.annotation.RestController
import tools.jackson.databind.JsonNode
import tools.jackson.databind.ObjectMapper
import tools.jackson.databind.node.ObjectNode

@Controller
class PageController {

    @GetMapping("/manager")
    fun manager(): String = "forward:/manager.html"

    @GetMapping("/m")
    fun audience(): String = "forward:/audience.html"
}

@RestController
class StatusController(
    private val link: FastApiLink,
    private val mapper: ObjectMapper
) {

    // FastAPI에서 받은 최신 상태 조회
    @GetMapping("/api/status")
    fun status(): ResponseEntity<JsonNode> {
        if (!link.connected) {
            return ResponseEntity.status(503)
                .body(detail("FastAPI 서버와 연결되어 있지 않아요"))
        }

        val current = link.latestStatus.get()
            ?: return ResponseEntity.status(503)
                .body(detail("FastAPI의 상태 정보를 기다리는 중이에요"))

        val copy = current.deepCopy() as ObjectNode
        val linkStats: JsonNode = mapper.valueToTree(link.stats())

        copy.set("web_link", linkStats)

        return ResponseEntity.ok(copy)
    }

    // 관리자 명령을 FastAPI로 전달
    @PostMapping("/api/control")
    fun control(
        @RequestBody body: Map<String, Any?>
    ): ResponseEntity<JsonNode> {
        val action = body["action"] as? String
            ?: return ResponseEntity.badRequest()
                .body(detail("action이 없어요"))

        if (action !in setOf("restrict", "open", "message", "auto")) {
            return ResponseEntity.badRequest()
                .body(detail("지원하지 않는 명령이에요"))
        }

        val command = mutableMapOf<String, Any?>(
            "type" to "control",
            "action" to action
        )

        when (action) {
            "restrict", "open" -> {
                val zone = body["zone"] as? String

                if (zone !in setOf("A", "B", "C")) {
                    return ResponseEntity.badRequest()
                        .body(detail("구역은 A, B, C 중 선택해 주세요"))
                }

                command["zone"] = zone
            }

            "message" -> {
                val text = (body["text"] as? String ?: "")
                    .trim()
                    .uppercase(java.util.Locale.ROOT)

                if (
                    text.isEmpty() ||
                    text.length > 20 ||
                    text.any { it.code !in 32..126 }
                ) {
                    return ResponseEntity.badRequest().body(
                        detail("LCD 문구는 영문·숫자·기호 20자 이내로 입력해 주세요")
                    )
                }

                command["text"] = text
            }
        }

        val sent = link.send(command, reliable = true)

        if (!sent) {
            return ResponseEntity.status(503)
                .body(detail("FastAPI 서버와 연결되어 있지 않아요"))
        }

        // 기존 화면이 기대하는 전체 상태 반환
        // 실제 명령 처리 완료는 이후 상태와 장치에서 확인
        try {
            Thread.sleep(300)
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()

            return ResponseEntity.status(503)
                .body(detail("명령은 전송 대기열에 등록됐지만 결과 확인이 중단됐어요"))
        }

        return status()
    }

    private fun detail(message: String): JsonNode {
        return mapper.createObjectNode().put("detail", message)
    }
}