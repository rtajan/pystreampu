#ifndef Wrapper_Gatherer_HPP_
#define Wrapper_Gatherer_HPP_

#include "wrapper/Common/pybind11_common.h"

#include <streampu.hpp>
#include <typeindex>
#include <vector>

namespace py = pybind11;

namespace spu
{
namespace module
{
/*!
 * \brief Gathers several sockets (of any types and sizes) into one socket of bytes (task 'gather').
 *
 * Used by the control flow structures of pyStreamPU to commute several variables with one Switcher.
 */
class Gatherer : public Stateful
{
  protected:
    std::vector<size_t> n_elmts;
    std::vector<std::type_index> datatypes;

  public:
    Gatherer(const std::vector<size_t>& n_elmts, const std::vector<std::type_index>& datatypes);
    virtual ~Gatherer() = default;
    virtual Gatherer* clone() const;
};

/*!
 * \brief Scatters one socket of bytes into several sockets (task 'scatter'), the reverse of 'Gatherer'.
 */
class Scatterer : public Stateful
{
  protected:
    std::vector<size_t> n_elmts;
    std::vector<std::type_index> datatypes;

  public:
    Scatterer(const std::vector<size_t>& n_elmts, const std::vector<std::type_index>& datatypes);
    virtual ~Scatterer() = default;
    virtual Scatterer* clone() const;
};
} // namespace module
} // namespace spu

namespace pyspu
{
namespace wrapper
{
void
wrap_gatherer(py::handle scope);
}
} // namespace pyspu
#endif // Wrapper_Gatherer_HPP_
