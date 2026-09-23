# Giao tiếp chọn lọc có nhận thức giá trị trong hệ thống đa tác tử LLM

Nghiên cứu thực nghiệm về giá trị của thông điệp trước khi truyền, sử dụng
các thí nghiệm phản thực có kiểm soát giữa những tác tử LLM chuyên biệt.

## Trạng thái nghiên cứu hiện tại — tháng 9/2026

| Thành phần nghiên cứu | Trạng thái |
| --- | --- |
| Môi trường nghiên cứu gồm ba tác tử | **HOÀN THÀNH** |
| Tách biệt thông tin riêng tư | **HOÀN THÀNH** |
| Bộ thí nghiệm phản thực WITH và WITHOUT | **HOÀN THÀNH** |
| Kiểm tra độ ổn định / độ tin cậy | **HOÀN THÀNH** |
| Pilot phân tầng theo sáu lớp nguyên nhân | **HOÀN THÀNH** |
| Counterfactual Message-Value Dataset v1 gồm 50 case | **HOÀN THÀNH** |
| Bộ ước lượng giá trị triển vọng V_hat | **CHƯA TRIỂN KHAI** |

Dataset v1 hiện có:

~~~text
50 case · 300 candidate message · 3.000 recipient trial · 300 mẫu tổng hợp
~~~

Repository này hiện được tổ chức chủ yếu như một implementation và testbed
phục vụ nghiên cứu. Ứng dụng điều tra tranh chấp Olist ban đầu là môi trường
thực nghiệm được dùng để xây dựng thiết kế nghiên cứu.

## 1. Bối cảnh nghiên cứu

Trong một hệ thống đa tác tử, các tác tử có thể trao đổi hoặc hợp nhất thông
tin mà không biết một thông điệp cụ thể có thực sự cải thiện quyết định tiếp
theo của tác tử nhận hay không.

Câu hỏi trung tâm của nghiên cứu là:

> Một tác tử có thể ước lượng liệu một candidate message có đáng được gửi đi
> trước khi truyền nó hay không?

Giai đoạn hiện tại đo giá trị thông điệp bằng các thí nghiệm phản thực có kiểm
soát. Bộ ước lượng triển vọng dự đoán giá trị trước khi truyền là bước tiếp
theo và chưa được triển khai.

## 2. Hệ thống ban đầu

Trước khi bắt đầu phần nghiên cứu, tôi đã có một hệ thống điều tra tranh chấp
thương mại điện tử dựa trên bộ dữ liệu công khai Brazilian Olist. Một tranh
chấp của khách hàng được phân tích bởi ba tác tử chuyên biệt; kết quả của các
tác tử được tổng hợp và kiểm tra bằng mã xác định.

Kiến trúc cấp cao của hệ thống ban đầu:

~~~text
Customer dispute
        |
        v
   Coordinator
        |
        +------------------+------------------+
        |                  |                  |
        v                  v                  v
 Order/Seller Agent    Payment Agent      Delivery Agent
        |                  |                  |
        +------------------+------------------+
                           |
                           v
                     Evidence Board
                           |
                           v
                      PolicyEngine
                           |
                           v
                        Verifier
                           |
                           v
                      Final result
~~~

Ba specialist agent có phạm vi bằng chứng riêng:

| Specialist agent | Bằng chứng được sử dụng |
| --- | --- |
| Order/Seller Agent | Trạng thái order, order item và bằng chứng seller |
| Payment Agent | Payment record, tổng tiền và cấu trúc thanh toán |
| Delivery Agent | Timeline giao hàng và các thời hạn vận chuyển |

PolicyEngine xác định sáu lớp nguyên nhân gốc:

1. canceled_order_paid
2. unavailable_order_paid
3. late_delivery_seller
4. late_delivery_logistics
5. valid_split_payment
6. unsupported_late_claim

PolicyEngine là mã xác định. Vì vậy, nó có thể được dùng làm oracle / ground
truth trong các thí nghiệm sau này. Ứng dụng ban đầu cung cấp môi trường dữ
liệu thực tế; câu hỏi nghiên cứu được kiểm tra trong research harness riêng.

## 3. Từ ứng dụng ban đầu đến research testbed

Hệ thống ban đầu được xây dựng để hoàn tất một cuộc điều tra. Nó chưa đo giá
trị của từng thông điệp giữa các tác tử. Để biến môi trường này thành một thí
nghiệm giao tiếp có kiểm soát, tôi đã tạo research harness độc lập tại
[experiments/pvoc_v0/](experiments/pvoc_v0/).

~~~text
Ứng dụng điều tra Olist ban đầu
        =
môi trường dữ liệu cho nghiên cứu

PVoC v0 harness
        =
bộ máy thí nghiệm để đo giá trị thông điệp
~~~

Ứng dụng production không phải là đóng góp nghiên cứu trực tiếp. Nó cung cấp
case thực tế, bằng chứng có cấu trúc, vai trò của các specialist agent và
oracle xác định. PVoC harness định nghĩa private observation, candidate
message cố định, các nhánh phản thực và phép đo cần thiết cho nghiên cứu.

## 4. Research testbed

Với mỗi grounded case, harness tạo ba private observation. Mỗi agent ra quyết
định từ observation của chính mình; sender có thể tạo một candidate message
cho một recipient.

~~~text
Olist case
   |
   +-------------------+-------------------+
   |                   |                   |
   v                   v                   v
Order/Seller        Payment            Delivery
private view        private view       private view
   |                   |                   |
   v                   v                   v
Agent A             Agent B            Agent C
   \                   |                   /
    +-------- candidate message --------+
                         |
                         v
                 WITH và WITHOUT trial
~~~

Với mỗi cặp sender-recipient có hướng, thí nghiệm thực hiện:

~~~mermaid
flowchart TD
    S[Sender decision] --> M[Candidate message m]
    M --> W[DELIVER m]
    M --> X[DROP m]
    W --> SW[Cùng recipient private observation<br/>+ message]
    X --> SX[Cùng recipient private observation<br/>+ không có message]
    SW --> DW[Decision WITH]
    SX --> DX[Decision WITHOUT]
    DW --> C[So sánh utility<br/>sau cả hai quyết định]
    DX --> C
    C --> V[Observed message value]
~~~

Các nguyên tắc kiểm soát:

- Mỗi agent chỉ nhận private observation của chính mình.
- Recipient không nhìn thấy global state, oracle hoặc private observation của
  sender.
- Điều kiện WITH chỉ bổ sung candidate message đã được freeze.
- Điều kiện WITHOUT dùng cùng recipient private observation nhưng không có
  message.
- Deep copy và observation fingerprint kiểm tra rằng state của recipient không
  thay đổi ngoài việc deliver message.
- PolicyEngine chỉ được dùng sau khi recipient đã ra quyết định để tính utility.

## 5. Ba agent và sáu communication edge

| Agent | Bằng chứng riêng tư | Vai trò trong thí nghiệm |
| --- | --- | --- |
| Order/Seller | Order status, item, seller record | Gửi hoặc nhận bằng chứng order và seller |
| Payment | Payment row, total và payment structure | Gửi hoặc nhận bằng chứng payment |
| Delivery | Delivery timeline và shipping deadline | Gửi hoặc nhận bằng chứng delivery |

Mỗi agent có thể gửi cho hai agent còn lại, tạo thành sáu directed edge:

~~~text
Order/Seller -> Payment
Order/Seller -> Delivery
Payment     -> Order/Seller
Payment     -> Delivery
Delivery    -> Order/Seller
Delivery    -> Payment
~~~

Mỗi candidate message gồm case identifier, sender, recipient, nội dung ngắn
và evidence identifier. Message của sender được tạo một lần và freeze trước
khi chạy các recipient trial lặp lại.

## 6. Cách đo giá trị thông điệp hiện tại

Single paired run ban đầu sử dụng:

~~~text
V_star = U_with - U_without - lambda * communication_cost
~~~

Các repeated trial sử dụng một đại lượng exploratory có tên riêng:

~~~text
delta_mean_utility = mean(U_with) - mean(U_without)
repeated_mean_value = delta_mean_utility - lambda * communication_cost
~~~

Utility hiện tại được cố ý giữ đơn giản:

~~~text
root cause đúng   = 1
root cause sai    = 0
~~~

Đây là observed counterfactual value: cả hai kết quả WITH và WITHOUT đều
được chạy thật. Nó chưa phải prospective estimator. Cụ thể, V_hat chưa dự
đoán giá trị trước khi message được truyền.

## 7. Các milestone đã hoàn thành

### Milestone 1 — Research harness và smoke test

**Trạng thái: HOÀN THÀNH**

Thí nghiệm thực đầu tiên đã thiết lập:

- ba private agent;
- sáu directed candidate message;
- nhánh counterfactual WITH và WITHOUT;
- kiểm tra fingerprint để bảo đảm cùng recipient state;
- PolicyEngine xác định làm oracle; và
- thực thi structured output thực tế qua Vertex/Gemini.

Run EC_001 đã tạo ra các khác biệt có ý nghĩa giữa những communication edge
khác nhau.

### Milestone 2 — Stability study

**Trạng thái: HOÀN THÀNH**

Stability study kiểm tra liệu communication effect quan sát được có chỉ là do
biến thiên khi sampling LLM hay không. Với EC_001, mỗi trong sáu edge được
chạy:

~~~text
10 WITH trial
10 WITHOUT trial
~~~

Tổng cộng có **120 recipient execution**. Message hash và observation
fingerprint cố định đều vượt qua validation. Modal share thấp nhất là **0.80**;
kết quả này đủ ổn định để chuyển sang pilot lớn hơn, đồng thời vẫn giữ lại
biến thiên quan sát được trong artifact.

### Milestone 3 — Stratified six-case pilot

**Trạng thái: HOÀN THÀNH**

Pilot sử dụng một case đại diện cho mỗi trong sáu root-cause class:

~~~text
6 case × 6 communication edge × (5 WITH + 5 WITHOUT)
= 360 recipient execution
~~~

Kết quả pilot quan sát được:

| Observed utility effect | Số lượng |
| --- | ---: |
| Positive | 10 |
| Zero | 24 |
| Negative | 2 |

Communication vì vậy không phải lúc nào cũng hữu ích trong pilot. Một số
message cải thiện quyết định, nhiều message không thay đổi utility, và một số
message làm giảm độ đúng của recipient. Đây là pilot evidence, không phải kết
luận về khả năng generalize.

### Milestone 4 — Counterfactual Message-Value Dataset v1

**Trạng thái: HOÀN THÀNH**

Dataset v1 sử dụng toàn bộ grounded case từ EC_001 đến EC_050:

| Root-cause class | Số case |
| --- | ---: |
| canceled_order_paid | 9 |
| unavailable_order_paid | 9 |
| late_delivery_seller | 8 |
| late_delivery_logistics | 8 |
| valid_split_payment | 8 |
| unsupported_late_claim | 8 |

Mỗi case đóng góp sáu frozen directed candidate message. Mỗi message được
đánh giá bằng năm WITH trial và năm WITHOUT trial:

~~~text
50 case
300 candidate message
3.000 recipient counterfactual trial
300 aggregated message-value sample
~~~

Phân bố utility-effect label thực nghiệm cuối cùng:

| Label | Số lượng |
| --- | ---: |
| POSITIVE | 74 |
| ZERO | 209 |
| NEGATIVE | 17 |

Các label này mô tả observed utility effect, tức delta_mean_utility. Chúng
chưa phải dự đoán của V_hat. Mean modal share của cả điều kiện WITH và WITHOUT
xấp xỉ **0.97**.

## 8. Một ví dụ nhỏ

Xét edge EC_001: Order/Seller -> Payment.

- **WITHOUT message:** Payment agent dự đoán valid_split_payment, nhưng đây là
  dự đoán sai vì oracle của case là canceled_order_paid.
- **WITH message:** message của Order/Seller cung cấp thông tin order đã bị
  hủy; Payment agent dự đoán canceled_order_paid và trở nên đúng.

Với candidate message này, utility quan sát được tăng từ 0 lên 1. Đây là một
positive observed downstream effect.

Ngược lại, ở edge Payment -> Order/Seller của EC_001, recipient đã dự đoán
đúng ngay cả khi không có message và vẫn đúng khi có message. Message không
cải thiện utility, dù vẫn phát sinh communication cost. Đây là một message
redundant theo utility hiện tại.

## 9. Một Dataset v1 sample chứa gì?

Về mặt khái niệm, mỗi row biểu diễn một case, sender, recipient và frozen
candidate message:

~~~text
(case, sender, recipient, candidate message)
        |
        +-- các kết quả lặp lại WITHOUT
        |
        +-- các kết quả lặp lại WITH
        |
        +-- delta_mean_utility
        |
        +-- communication cost
        +-- repeated_mean_value
~~~

Artifact tổng hợp chính:

~~~text
experiments/pvoc_v0/results/dataset_v1/
pvoc_v1_20260922T205457Z_483f9ca0/dataset.jsonl
~~~

Cùng run directory còn có frozen message, raw recipient trial,
case summary, manifest và summary để phục vụ reproducibility.

## 10. Tiến độ so với proposal

~~~mermaid
flowchart TD
    A[Môi trường nghiên cứu<br/>ba grounded agent] --> B[Tách biệt private information]
    B --> C[Bộ máy counterfactual SEND và DROP]
    C --> D[Stability / reliability validation]
    D --> E[Six-case pilot]
    E --> F[Counterfactual message-value dataset]
    F --> G[Prospective V_hat estimator<br/>BƯỚC TIẾP THEO]
    G --> H[Selective communication policy]
    H --> I[Baselines, budget, ablation,<br/>generalization và final evaluation]
    classDef done fill:#dcfce7,stroke:#15803d,color:#14532d;
    classDef next fill:#fef3c7,stroke:#b45309,color:#78350f;
    classDef later fill:#f3f4f6,stroke:#6b7280,color:#374151;
    class A,B,C,D,E,F done;
    class G next;
    class H,I later;
~~~

Dự án đã hoàn tất giai đoạn đo lường: có thể đo observed effect thật của một
candidate message bằng cách chạy cả hai điều kiện phản thực. Giai đoạn dự đoán
prospective vẫn chưa hoàn tất.

## 11. Vị trí hiện tại trong câu hỏi nghiên cứu

Những gì hiện có:

~~~text
candidate message
        |
        +--> chạy thật ở điều kiện WITH
        |
        +--> chạy thật ở điều kiện WITHOUT
        |
        v
observed counterfactual value
~~~

Những gì nghiên cứu cuối cùng cần có:

~~~text
candidate message
        |
        v
prospective estimator V_hat
        |
        +---- predicted value cao ---> SEND
        |
        +---- predicted value thấp --> DROP
~~~

V_hat **chưa được triển khai**. Hiện chưa có production selective router hoặc
SEND/DROP policy.

## 12. Các bước tiếp theo

Bước tiếp theo là định nghĩa bài toán dự đoán không bị leakage từ Dataset v1.
Thứ tự dự kiến:

1. **Xác định pre-send feature hợp lệ.** Feature phải có sẵn với sender trước
   khi communication xảy ra. Feature không được chứa downstream outcome, oracle
   label không có sẵn tại thời điểm gửi, kết quả của WITH condition hoặc thông
   tin tương lai khác.
2. **Thiết lập baseline prospective đơn giản.** Có thể xem xét heuristic,
   supervised predictor đơn giản hoặc LLM-based value estimator. Phương pháp
   cuối cùng chưa được chọn.
3. **Train và evaluate V_hat.** Mục tiêu là dự đoán observed message value
   trước khi message được truyền.
4. **Chuyển dự đoán thành selective communication.** Policy tương lai có thể
   gửi khi V_hat(m) > threshold và DROP trong trường hợp ngược lại.
5. **So sánh với communication baseline:** full communication, no
   communication, random hoặc budgeted communication, và existing merge-late
   behavior nếu phù hợp.
6. **Đánh giá trade-off:** task correctness, số message, token, latency,
   communication cost và mức performance được giữ lại dưới các budget khác nhau.
7. **Về sau, đánh giá ablation và generalization** trên recipient, root-cause
   type, team configuration và — nếu đủ tài nguyên — model hoặc task khác.

Chưa có bước prospective estimation hoặc selective routing nào được tuyên bố
là đã hoàn thành.

## 13. Bản đồ repository dành cho nghiên cứu

~~~text
multiAgent_research/
|
|-- experiments/pvoc_v0/
|   |-- core/          # research primitive dùng chung
|   |-- studies/       # smoke, stability, pilot, Dataset v1
|   |-- results/       # experiment artifact đã freeze
|   |-- cli.py         # entry point thống nhất
|
|-- data/input/        # grounded EC case input
|-- src/               # ứng dụng Olist và experimental environment ban đầu
|-- tests/experiments/ # research test
~~~

Supervisor quan tâm đến research nên bắt đầu từ
[experiments/pvoc_v0/](experiments/pvoc_v0/). README bên trong thư mục này
chứa technical run command và artifact detail; root README này tập trung giải
thích research story và tiến độ hiện tại.

## Ranh giới nghiên cứu hiện tại

- Dataset v1 là observed-value dataset, chưa phải learned value estimator.
- Utility hiện tại là binary correctness so với deterministic oracle.
- Các thí nghiệm mới được thực hiện trong một Olist experimental environment;
  chưa có kết luận về generalization sang môi trường khác.
- Chưa có learned V_hat estimator.
- Chưa có production selective communication router.
- Chưa có kết luận causal proof hoặc selective communication policy hoàn chỉnh.

