#include "wrapper/Module/Stateful/Gatherer/Gatherer.hpp"
#include "wrapper/Common/Tools/Type/Type.hpp"
#include "wrapper/Common/Tools/type_functions.h"

#include <cstring>
#include <sstream>
#include <string>

namespace py = pybind11;
using namespace py::literals;
using namespace spu;
using namespace spu::module;

namespace
{
void
check_specs(const std::vector<size_t>& n_elmts, const std::vector<std::type_index>& datatypes)
{
    if (n_elmts.empty() || n_elmts.size() != datatypes.size())
    {
        std::stringstream message;
        message << "'n_elmts' and 'datatypes' must have the same non-zero size ('n_elmts.size()' = " << n_elmts.size()
                << ", 'datatypes.size()' = " << datatypes.size() << ").";
        throw tools::invalid_argument(__FILE__, __LINE__, __func__, message.str());
    }
    for (auto n : n_elmts)
        if (n == 0)
        {
            std::stringstream message;
            message << "Each socket must have at least one element.";
            throw tools::invalid_argument(__FILE__, __LINE__, __func__, message.str());
        }
}

// Number of bytes of each socket for one call of the codelet (one wave of frames)
inline size_t
wave_bytes(const runtime::Socket& s, const Module& m)
{
    return s.get_databytes() / m.get_n_frames() * m.get_n_frames_per_wave();
}
} // namespace

Gatherer::Gatherer(const std::vector<size_t>& n_elmts, const std::vector<std::type_index>& datatypes)
  : Stateful()
  , n_elmts(n_elmts)
  , datatypes(datatypes)
{
    check_specs(n_elmts, datatypes);
    this->set_name("Gatherer");
    this->set_short_name("Gatherer");

    auto& t = this->create_task("gather");
    std::vector<size_t> sids;
    size_t n_bytes = 0;
    for (size_t i = 0; i < n_elmts.size(); i++)
    {
        sids.push_back(this->create_socket_in(t, "in" + std::to_string(i), n_elmts[i], datatypes[i]));
        n_bytes += n_elmts[i] * t.sockets[sids.back()]->get_datatype_size();
    }
    const size_t sid_out = this->create_socket_out(t, "out", n_bytes, typeid(uint8_t));

    this->create_codelet(t,
                         [sids, sid_out](Module& m, runtime::Task& t, const size_t /*frame_id*/) -> int
                         {
                             // the frames of each input are contiguous: the output holds, for each frame,
                             // the bytes of in0, then the bytes of in1...
                             const size_t n_frames = m.get_n_frames_per_wave();
                             uint8_t* out = (uint8_t*)t[sid_out].get_dataptr();
                             const size_t out_frame = wave_bytes(t[sid_out], m) / n_frames;
                             size_t offset = 0;
                             for (auto sid : sids)
                             {
                                 const uint8_t* in = (const uint8_t*)t[sid].get_dataptr();
                                 const size_t in_frame = wave_bytes(t[sid], m) / n_frames;
                                 for (size_t f = 0; f < n_frames; f++)
                                     std::memcpy(out + f * out_frame + offset, in + f * in_frame, in_frame);
                                 offset += in_frame;
                             }
                             return runtime::status_t::SUCCESS;
                         });
}

Gatherer*
Gatherer::clone() const
{
    auto m = new Gatherer(*this);
    m->deep_copy(*this);
    return m;
}

Scatterer::Scatterer(const std::vector<size_t>& n_elmts, const std::vector<std::type_index>& datatypes)
  : Stateful()
  , n_elmts(n_elmts)
  , datatypes(datatypes)
{
    check_specs(n_elmts, datatypes);
    this->set_name("Scatterer");
    this->set_short_name("Scatterer");

    auto& t = this->create_task("scatter");
    size_t n_bytes = 0;
    for (size_t i = 0; i < n_elmts.size(); i++)
        n_bytes += n_elmts[i] * runtime::type_to_size.at(datatypes[i]);
    const size_t sid_in = this->create_socket_in(t, "in", n_bytes, typeid(uint8_t));
    std::vector<size_t> sids;
    for (size_t i = 0; i < n_elmts.size(); i++)
        sids.push_back(this->create_socket_out(t, "out" + std::to_string(i), n_elmts[i], datatypes[i]));

    this->create_codelet(t,
                         [sids, sid_in](Module& m, runtime::Task& t, const size_t /*frame_id*/) -> int
                         {
                             const size_t n_frames = m.get_n_frames_per_wave();
                             const uint8_t* in = (const uint8_t*)t[sid_in].get_dataptr();
                             const size_t in_frame = wave_bytes(t[sid_in], m) / n_frames;
                             size_t offset = 0;
                             for (auto sid : sids)
                             {
                                 uint8_t* out = (uint8_t*)t[sid].get_dataptr();
                                 const size_t out_frame = wave_bytes(t[sid], m) / n_frames;
                                 for (size_t f = 0; f < n_frames; f++)
                                     std::memcpy(out + f * out_frame, in + f * in_frame + offset, out_frame);
                                 offset += out_frame;
                             }
                             return runtime::status_t::SUCCESS;
                         });
}

Scatterer*
Scatterer::clone() const
{
    auto m = new Scatterer(*this);
    m->deep_copy(*this);
    return m;
}

namespace
{
std::vector<std::type_index>
to_typeids(const std::vector<pyspu::dtype>& dtypes)
{
    std::vector<std::type_index> out;
    for (auto& d : dtypes)
        out.push_back(pyspu::utils::str2typeid(d.get_name()));
    return out;
}
} // namespace

void
pyspu::wrapper::wrap_gatherer(py::handle scope)
{
    auto gatherer_class = py::class_<spu::module::Gatherer, spu::module::Stateful>(scope, "Gatherer", R"pbdoc(
        Module that gathers several sockets, of any types and sizes, into one socket of bytes
        (task `gather`, inputs `in0`, `in1`..., output `out` of uint8). For each frame, the
        output holds the bytes of `in0`, then the bytes of `in1`...

        Used by the control flow structures (`If`, `Switch`, `For`, `While`...) to handle
        several variables with one Switcher. `Scatterer` does the reverse.
    )pbdoc");
    gatherer_class.def(py::init([](const std::vector<size_t>& n_elmts, const std::vector<pyspu::dtype>& datatypes)
                                { return new spu::module::Gatherer(n_elmts, to_typeids(datatypes)); }),
                       "n_elmts"_a,
                       "datatypes"_a,
                       R"pbdoc(
        Build a gatherer.

        Args:
            n_elmts (list[int]): number of elements (per frame) of each input
            datatypes (list[dtype]): data type of each input
    )pbdoc",
                       py::return_value_policy::take_ownership);

    auto scatterer_class = py::class_<spu::module::Scatterer, spu::module::Stateful>(scope, "Scatterer", R"pbdoc(
        Module that scatters one socket of bytes into several sockets (task `scatter`, input
        `in` of uint8, outputs `out0`, `out1`...): the reverse of `Gatherer`.
    )pbdoc");
    scatterer_class.def(py::init([](const std::vector<size_t>& n_elmts, const std::vector<pyspu::dtype>& datatypes)
                                 { return new spu::module::Scatterer(n_elmts, to_typeids(datatypes)); }),
                        "n_elmts"_a,
                        "datatypes"_a,
                        R"pbdoc(
        Build a scatterer.

        Args:
            n_elmts (list[int]): number of elements (per frame) of each output
            datatypes (list[dtype]): data type of each output
    )pbdoc",
                        py::return_value_policy::take_ownership);
}
