// FastApiLink.kt - 시연 담당: 코틀린 웹 서버(형진) <-> FastAPI 서버(수경) TCP 통신
//
// tcp_link.py와 똑같은 응용계층 규격 (언어가 달라도 통함)
//  - 메시지 = JSON 한 줄 + 줄바꿈(\n)
//  - 중요한 메시지는 "id"를 붙여 보내고, 받는 쪽은 {"type":"ack","id":..}로 답함
//    2초 안에 ack가 없으면 최대 3번 재전송, 같은 id는 한 번만 처리
//  - 5초마다 heartbeat, 15초 동안 아무것도 안 오면 끊김 → 3초 뒤 재접속
//
// 스레드 3개 (full-duplex)
//  - link-recv  : 접속 + 받기 (끊기면 재접속)
//  - link-send  : 큐에 쌓인 메시지 보내기
//  - link-watch : 재전송, 하트비트, 무응답 감지
//
// ※ 맨 위 package 줄은 형진이 프로젝트 패키지 이름으로 바꾸기
package com.team.crowd

import com.fasterxml.jackson.databind.JsonNode
import com.fasterxml.jackson.databind.ObjectMapper
import jakarta.annotation.PostConstruct
import org.springframework.beans.factory.annotation.Value
import org.springframework.stereotype.Component
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetSocketAddress
import java.net.Socket
import java.nio.charset.StandardCharsets
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.ConcurrentLinkedDeque
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference
import kotlin.concurrent.thread

@Component
class FastApiLink(
    private val mapper: ObjectMapper,
    @Value("\${fastapi.host:127.0.0.1}") private val host: String,
    @Value("\${fastapi.link-port:5002}") private val port: Int,
) {
    /** FastAPI가 1초마다 보내 주는 화면용 전체 상태 (최신 값) */
    val latestStatus = AtomicReference<JsonNode?>(null)

    @Volatile var connected = false
        private set
    @Volatile private var socket: Socket? = null
    @Volatile private var lastRx = 0L
    @Volatile private var lastHeartbeat = 0L
    @Volatile var retries = 0
        private set

    private class Pending(val json: String, var sentAt: Long, var tries: Int)

    private val outbox = LinkedBlockingQueue<String>()
    private val pending = ConcurrentHashMap<Int, Pending>()
    private val seen = ArrayDeque<Int>()
    private val nextId = AtomicInteger(1)
    private val rttMs = ConcurrentLinkedDeque<Long>()

    @PostConstruct
    fun start() {
        thread(isDaemon = true, name = "link-recv") { connectLoop() }
        thread(isDaemon = true, name = "link-send") { sendLoop() }
        thread(isDaemon = true, name = "link-watch") { watchLoop() }
    }

    // ---------------- 보내기 ----------------
    /** reliable = true 이면 ack를 기다리고, 안 오면 재전송한다 */
    fun send(msg: Map<String, Any?>, reliable: Boolean = false): Boolean {
        if (!connected) return false
        if (reliable) {
            val id = nextId.getAndIncrement()
            val json = mapper.writeValueAsString(msg + ("id" to id))
            pending[id] = Pending(json, now(), 0)
            outbox.put(json)
        } else {
            outbox.put(mapper.writeValueAsString(msg))
        }
        return true
    }

    private fun sendLoop() {
        while (true) {
            val json = outbox.poll(500, TimeUnit.MILLISECONDS) ?: continue
            val s = socket
            if (s == null || !connected) continue
            try {
                val out = s.getOutputStream()
                out.write((json + "\n").toByteArray(StandardCharsets.UTF_8))
                out.flush()
            } catch (e: Exception) {
                close("보내기 실패")
            }
        }
    }

    // ---------------- 접속 + 받기 ----------------
    private fun connectLoop() {
        while (true) {
            try {
                val s = Socket()
                s.connect(InetSocketAddress(host, port), 3000)
                socket = s
                lastRx = now()
                connected = true
                println("[FastApiLink] FastAPI $host:$port 접속 성공")
                send(mapOf("type" to "hello", "role" to "web"))
                val reader = BufferedReader(InputStreamReader(s.getInputStream(), StandardCharsets.UTF_8))
                while (true) {
                    val line = reader.readLine() ?: break          // 상대가 끊으면 null
                    lastRx = now()
                    if (line.isBlank()) continue
                    val msg = try { mapper.readTree(line) } catch (e: Exception) { continue }
                    handle(msg)
                }
            } catch (e: Exception) {
                // 접속 실패 또는 소켓이 닫힘
            }
            close("연결 끊김")
            println("[FastApiLink] 3초 뒤 다시 접속")
            Thread.sleep(3000)
        }
    }

    private fun handle(msg: JsonNode) {
        when (msg.path("type").asText()) {
            "ack" -> pending.remove(msg.path("id").asInt())?.let {
                rttMs.addLast(now() - it.sentAt)
                while (rttMs.size > 50) rttMs.pollFirst()
            }
            "heartbeat" -> outbox.put(mapper.writeValueAsString(mapOf("type" to "heartbeat_ack")))
            "heartbeat_ack" -> {}
            else -> {
                if (msg.has("id")) {                                  // ack가 필요한 메시지
                    val id = msg.path("id").asInt()
                    outbox.put(mapper.writeValueAsString(mapOf("type" to "ack", "id" to id)))
                    synchronized(seen) {
                        if (seen.contains(id)) return                 // 재전송으로 또 온 것 → 무시
                        seen.addLast(id)
                        if (seen.size > 500) seen.removeFirst()
                    }
                }
                if (msg.path("type").asText() == "status") latestStatus.set(msg.path("data"))
            }
        }
    }

    // ---------------- 감시: 재전송 / 하트비트 / 무응답 ----------------
    private fun watchLoop() {
        while (true) {
            Thread.sleep(200)
            if (!connected) continue
            val t = now()
            for ((id, p) in pending) {
                if (t - p.sentAt < 2000) continue
                if (p.tries >= 3) {
                    pending.remove(id)
                    println("[FastApiLink] id $id 재전송 3번 실패 → 포기")
                } else {
                    p.sentAt = t
                    p.tries++
                    retries++
                    println("[FastApiLink] id $id ack 없음 → 재전송 ${p.tries}번째")
                    outbox.put(p.json)
                }
            }
            if (t - lastHeartbeat >= 5000) {
                lastHeartbeat = t
                outbox.put(mapper.writeValueAsString(mapOf("type" to "heartbeat")))
            }
            if (t - lastRx >= 15000) close("15초 동안 응답 없음")
        }
    }

    @Synchronized
    private fun close(reason: String) {
        if (socket == null) return
        connected = false
        try { socket?.close() } catch (e: Exception) {}
        socket = null
        pending.clear()
        println("[FastApiLink] 연결 종료: $reason")
    }

    fun stats(): Map<String, Any?> {
        val r = rttMs.toList()
        return mapOf(
            "connected" to connected,
            "last_rx_ago_s" to if (lastRx == 0L) null else (now() - lastRx) / 1000.0,
            "rtt_last_ms" to r.lastOrNull(),
            "rtt_avg_ms" to if (r.isEmpty()) null else r.average().toLong(),
            "retries" to retries,
            "pending" to pending.size,
        )
    }

    private fun now() = System.currentTimeMillis()
}
